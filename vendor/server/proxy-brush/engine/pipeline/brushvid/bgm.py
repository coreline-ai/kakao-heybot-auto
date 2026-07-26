"""로컬 BGM 카탈로그, 라이선스 증빙, import/preflight 공통 로직.

빌드 중에는 네트워크를 사용하지 않는다. 음원은 공식 페이지에서 사람이 먼저 내려받고
``bin/bgm-assets.py import`` 로 등록한 파일만 사용한다.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

from .project import BgmConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "assets" / "bgm" / "catalog.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "assets" / "bgm" / "catalog.schema.json"
LOCAL_BGM_ROOT = REPO_ROOT / "local-assets" / "bgm"
CONTENT_ID_STATUSES = ("registered", "not-displayed", "unknown", "verified-not-registered")
YOUTUBE_DISTRIBUTIONS = frozenset(("youtube", "shorts"))


class BgmAssetError(ValueError):
    """카탈로그·로컬 음원·증빙 계약 위반."""


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def probe_audio(path: str | Path) -> dict[str, Any]:
    """ffprobe로 실제 오디오 스트림·길이·코덱을 검증한다."""
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise BgmAssetError(f"오디오 파일을 읽을 수 없음: {path}") from exc
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        raise BgmAssetError(f"ffprobe JSON 파싱 실패: {path}") from exc
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise BgmAssetError(f"오디오 스트림 없음: {path}")
    duration = float(data.get("format", {}).get("duration") or audio.get("duration") or 0)
    if duration <= 0:
        raise BgmAssetError(f"오디오 길이 확인 실패: {path}")
    return {
        "durationSec": duration,
        "codec": audio.get("codec_name"),
        "sampleRate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
    }


def load_catalog(path: str | Path = CATALOG_PATH,
                 schema_path: str | Path = CATALOG_SCHEMA_PATH) -> dict:
    path, schema_path = Path(path), Path(schema_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.absolute_path) or "<root>"
        raise BgmAssetError(f"BGM catalog 검증 실패 {where}: {first.message}")
    ids = [a["id"] for a in data["assets"]]
    if len(ids) != len(set(ids)):
        raise BgmAssetError("BGM catalog에 중복 assetId가 있음")
    return data


def catalog_map(catalog: dict) -> dict[str, dict]:
    return {item["id"]: item for item in catalog["assets"]}


def _safe_local_path(entry: dict, repo_root: str | Path = REPO_ROOT) -> Path:
    root = (Path(repo_root) / "local-assets" / "bgm").resolve()
    path = (Path(repo_root) / entry["localPath"]).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BgmAssetError(f"localPath가 local-assets/bgm 밖을 가리킴: {entry['localPath']}") from exc
    return path


def inspect_entry(entry: dict, repo_root: str | Path = REPO_ROOT,
                  *, today: date | None = None) -> dict:
    """단일 엔트리의 파일·해시·증빙·Content ID 상태를 검사한다."""
    today = today or date.today()
    errors: list[str] = []
    warnings: list[str] = []
    path = _safe_local_path(entry, repo_root)
    asset_dir = path.parent.resolve()
    audio_exists = path.is_file() and path.stat().st_size > 0
    hash_ok = False

    if not entry.get("downloaded"):
        errors.append("catalog downloaded=false")
    if not audio_exists:
        errors.append(f"로컬 음원 없음: {path}")
    elif not entry.get("sha256"):
        errors.append("SHA-256 누락")
    else:
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            errors.append(f"SHA-256 불일치: catalog={entry['sha256']} actual={actual}")
        else:
            hash_ok = True
    if not entry.get("artist"):
        errors.append("작가명 누락")

    license_info = entry.get("license") or {}
    if not license_info.get("downloadedAt"):
        errors.append("다운로드 날짜 누락")
    checked_at = license_info.get("checkedAt")
    if not checked_at:
        errors.append("라이선스/Content ID 확인 날짜 누락")
    else:
        try:
            age = (today - date.fromisoformat(checked_at)).days
            if age > 90:
                warnings.append(f"라이선스/Content ID 확인 후 {age}일 경과")
        except ValueError:
            errors.append(f"checkedAt 날짜 형식 오류: {checked_at}")

    status = license_info.get("contentIdStatus", "unknown")
    if status == "unknown":
        errors.append("Content ID 상태 unknown")
    elif status == "registered":
        errors.append("Content ID registered 음원은 기본 정책에서 사용 불가")
    elif status == "not-displayed":
        warnings.append("페이지에 Content ID 표시가 없었으나 미등록을 보장하지 않음")

    attribution_required = bool(license_info.get("attributionRequired", False))
    attribution_text = license_info.get("attributionText")
    if attribution_required and (not isinstance(attribution_text, str) or not attribution_text.strip()):
        errors.append("저작자 표시 필수 음원의 attributionText 누락")

    evidence = license_info.get("evidenceFiles") or []
    source_count = license_count = 0
    evidence_files_ok = True
    for rel in evidence:
        p = (asset_dir / rel).resolve()
        try:
            p.relative_to(asset_dir)
        except ValueError:
            errors.append(f"증빙 경로가 asset 디렉터리 밖을 가리킴: {rel}")
            continue
        if not p.is_file() or p.stat().st_size == 0:
            errors.append(f"증빙 파일 없음: {p}")
            evidence_files_ok = False
        name = p.name.lower()
        source_count += int(name.startswith("source-page"))
        license_count += int(name.startswith("license"))
    if source_count < 1:
        errors.append("곡 페이지 증빙 누락(source-page.*)")
    if license_count < 1:
        errors.append("라이선스 증빙 누락(license.*)")

    if path.is_file() and not errors:
        try:
            media = probe_audio(path)
            if abs(media["durationSec"] - float(entry["durationSec"])) > 2.0:
                errors.append(
                    f"카탈로그/실측 길이 차이 >2초: {entry['durationSec']} vs {media['durationSec']:.2f}"
                )
        except BgmAssetError as exc:
            errors.append(str(exc))

    checks = {"audio": audio_exists, "hash": hash_ok,
              "evidence": source_count >= 1 and license_count >= 1 and evidence_files_ok,
              "metadata": bool(
                  entry.get("artist") and license_info.get("downloadedAt") and checked_at
                  and (not attribution_required or (isinstance(attribution_text, str) and attribution_text.strip()))
              )}
    return {"id": entry["id"], "path": str(path),
            "youtubeAllowed": bool(entry.get("youtubeAllowed", False)),
            "errors": errors, "warnings": warnings,
            "checks": checks, "ok": not errors}


def selected_asset_ids(config: BgmConfig) -> tuple[str, ...]:
    if config.mode == "asset":
        return (config.asset_id,) if config.asset_id else ()
    if config.mode == "playlist":
        return config.asset_ids
    return ()


# 대사(음성/TTS)가 없는 비공개/내부 영상용 기존 로컬 BGM 기본 정책.
# Pixabay 자산은 과거 검증과 내부 청취용으로만 보존하며 YouTube/Shorts에는 사용하지 않는다.
AUTO_BGM_BY_PROFILE = {
    "brush": "pixabay-gentle-piano-meditation",
    "pen": "pixabay-digital-ambient-meditation",
    "pen-brush": "pixabay-piano-dreamcloud-meditation",
    "cosmic-random-brush": "pixabay-gentle-piano-meditation",
}
AUTO_BGM_SHORTS = "pixabay-summer-breeze-meditation"          # 밝은 세로 쇼츠
AUTO_BGM_PLAYLIST = (
    "pixabay-gentle-piano-meditation",
    "pixabay-autumn-sky-meditation",
    "pixabay-summer-breeze-meditation",
)
YOUTUBE_AUTO_BGM_BY_PROFILE = {
    "brush": "youtube-chris-zabriskie-fight-for-your-honor",
    "pen": "youtube-chris-zabriskie-chance-luck-finale",
    "pen-brush": "youtube-jesse-gallagher-satya-yuga",
    "cosmic-random-brush": "youtube-chris-zabriskie-fight-for-your-honor",
}
YOUTUBE_AUTO_BGM_SHORTS = "youtube-jesse-gallagher-satya-yuga"
YOUTUBE_AUTO_BGM_PLAYLIST = (
    "youtube-chris-zabriskie-fight-for-your-honor",
    "youtube-chris-zabriskie-chance-luck-finale",
    "youtube-jesse-gallagher-satya-yuga",
)
AUTO_BGM_LONG_SEC = 600.0


def select_auto_bgm(*, profile: str = "brush", fmt: str = "youtube",
                    duration_sec: float = 0.0) -> BgmConfig:
    """대사 없는 영상용 기본 BGM 을 결정적으로 선택한다.

    - YouTube/Shorts: Pixabay 금지, 허용된 YouTube Audio Library/CC BY 자산만 선택
    - 10분(600초) 초과: 배포 형식별 2~3곡 playlist
    - shorts: 배포 형식에 맞는 단일곡
    - 그 외: 프로파일별 기본곡(미지정 프로파일은 brush 기본)
    반환값은 명시 ``bgm:`` 블록과 동일한 ``BgmConfig`` 이며 preflight 대상이다.
    """
    if fmt in YOUTUBE_DISTRIBUTIONS:
        if duration_sec > AUTO_BGM_LONG_SEC:
            return BgmConfig(mode="playlist", asset_ids=YOUTUBE_AUTO_BGM_PLAYLIST)
        if fmt == "shorts":
            return BgmConfig(mode="asset", asset_id=YOUTUBE_AUTO_BGM_SHORTS)
        return BgmConfig(
            mode="asset",
            asset_id=YOUTUBE_AUTO_BGM_BY_PROFILE.get(
                profile, YOUTUBE_AUTO_BGM_BY_PROFILE["brush"]),
        )
    if duration_sec > AUTO_BGM_LONG_SEC:
        return BgmConfig(mode="playlist", asset_ids=AUTO_BGM_PLAYLIST)
    if fmt == "shorts":
        return BgmConfig(mode="asset", asset_id=AUTO_BGM_SHORTS)
    return BgmConfig(mode="asset", asset_id=AUTO_BGM_BY_PROFILE.get(profile, AUTO_BGM_BY_PROFILE["brush"]))


def preflight_assets(config: BgmConfig, *, distribution: str | None = None,
                     repo_root: str | Path = REPO_ROOT,
                     catalog_path: str | Path = CATALOG_PATH,
                     schema_path: str | Path = CATALOG_SCHEMA_PATH) -> list[dict]:
    """명시된 외부 BGM을 렌더 전에 검증한다.

    YouTube/Shorts 배포에는 Pixabay 음원을 허용하지 않으며 ``warn`` 정책으로도 완화할 수 없다.
    파일 누락과 해시·증빙 오류 역시 정책과 무관하게 실패한다.
    """
    ids = selected_asset_ids(config)
    if not ids:
        return []
    catalog = load_catalog(catalog_path, schema_path)
    by_id = catalog_map(catalog)
    resolved: list[dict] = []
    failures: list[str] = []
    for asset_id in ids:
        entry = by_id.get(asset_id)
        if entry is None:
            failures.append(f"알 수 없는 BGM assetId: {asset_id}")
            continue
        if distribution in YOUTUBE_DISTRIBUTIONS and not entry.get("youtubeAllowed", False):
            failures.append(
                f"{asset_id}: Pixabay 음원은 YouTube/Shorts 제작·교체·배포에 사용 금지"
            )
        check = inspect_entry(entry, repo_root)
        hard = [e for e in check["errors"] if "로컬 음원 없음" in e or "SHA-256" in e or "오디오" in e]
        if config.license_policy == "strict":
            failures.extend(f"{asset_id}: {e}" for e in check["errors"])
        else:
            failures.extend(f"{asset_id}: {e}" for e in hard)
            check["warnings"].extend(e for e in check["errors"] if e not in hard)
        resolved.append({**entry, "resolvedPath": check["path"], "preflight": check})
    if failures:
        raise BgmAssetError("BGM preflight 실패:\n- " + "\n- ".join(failures))
    return resolved


def write_license_manifest(out_path: str | Path, project_id: str, config: BgmConfig,
                           assets: list[dict], *, distribution: str | None = None) -> Path:
    payload = {
        "schemaVersion": 1,
        "projectId": project_id,
        "distribution": distribution,
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "licensePolicy": config.license_policy,
        "bgmConfig": asdict(config),
        "notice": (
            "Pixabay 음원은 YouTube/Shorts 제작·교체·배포에 사용 금지입니다. "
            "Content ID not-displayed는 미등록을 보장하지 않습니다. 공개 업로드 전 다시 확인하고, "
            "attributionRequired=true 음원은 attributionText를 영상 설명란/크레딧에 그대로 포함하세요."
        ),
        "assets": [
            {k: v for k, v in asset.items() if k not in ("resolvedPath", "preflight")}
            | {"resolvedPath": asset["resolvedPath"], "preflightWarnings": asset["preflight"]["warnings"]}
            for asset in assets
        ],
    }
    out = Path(out_path)
    _atomic_json(out, payload)
    return out


def import_asset(asset_id: str, source_file: str | Path, *, artist: str,
                 content_id_status: str, source_evidence: str | Path,
                 license_evidence: str | Path, certificate: str | Path | None = None,
                 downloaded_at: str | None = None, checked_at: str | None = None,
                 replace: bool = False, repo_root: str | Path = REPO_ROOT,
                 catalog_path: str | Path = CATALOG_PATH,
                 schema_path: str | Path = CATALOG_SCHEMA_PATH) -> dict:
    """이미 공식 다운로드한 음원과 증빙을 로컬 저장소에 등록한다."""
    if content_id_status not in CONTENT_ID_STATUSES:
        raise BgmAssetError(f"지원하지 않는 Content ID 상태: {content_id_status}")
    if not artist.strip():
        raise BgmAssetError("작가명은 필수")
    src = Path(source_file).expanduser().resolve()
    source_ev = Path(source_evidence).expanduser().resolve()
    license_ev = Path(license_evidence).expanduser().resolve()
    for p, label in ((src, "음원"), (source_ev, "곡 페이지 증빙"), (license_ev, "라이선스 증빙")):
        if not p.is_file() or p.stat().st_size == 0:
            raise BgmAssetError(f"{label} 파일 없음: {p}")
    if src.suffix.lower() != ".mp3":
        raise BgmAssetError(f"공식 MP3 파일만 등록할 수 있음: {src}")
    media = probe_audio(src)
    if media["codec"] != "mp3":
        raise BgmAssetError(f"확장자와 실제 코덱 불일치(MP3 아님): {src} ({media['codec']})")
    digest = sha256_file(src)

    catalog = load_catalog(catalog_path, schema_path)
    by_id = catalog_map(catalog)
    if asset_id not in by_id:
        raise BgmAssetError(f"catalog에 없는 assetId: {asset_id}")
    entry = by_id[asset_id]
    target = _safe_local_path(entry, repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha256_file(target) != digest and not replace:
        raise BgmAssetError(f"다른 파일이 이미 등록됨: {target} (--replace 필요)")
    tmp = target.with_suffix(target.suffix + ".part")
    shutil.copy2(src, tmp)
    if sha256_file(tmp) != digest:
        tmp.unlink(missing_ok=True)
        raise BgmAssetError("로컬 복사 후 SHA-256 불일치")
    tmp.replace(target)

    evidence_dir = target.parent / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source, stem in ((source_ev, "source-page"), (license_ev, "license")):
        dest = evidence_dir / f"{stem}{source.suffix.lower()}"
        # attach는 이미 local-assets 안에 캡처된 증빙을 그대로 재사용한다.
        # source와 dest가 같은 파일이면 copy2가 SameFileError를 내므로 복사를 생략한다.
        if source != dest.resolve():
            shutil.copy2(source, dest)
        copied.append(str(dest.relative_to(target.parent)))
    if certificate is not None:
        cert = Path(certificate).expanduser().resolve()
        if not cert.is_file():
            raise BgmAssetError(f"Content ID 인증서 파일 없음: {cert}")
        dest = evidence_dir / f"content-id-certificate{cert.suffix.lower()}"
        shutil.copy2(cert, dest)
        copied.append(str(dest.relative_to(target.parent)))

    today = date.today().isoformat()
    entry["artist"] = artist.strip()
    entry["durationSec"] = round(media["durationSec"], 3)
    entry["downloaded"] = True
    entry["sha256"] = digest
    entry["license"]["downloadedAt"] = downloaded_at or today
    entry["license"]["checkedAt"] = checked_at or today
    entry["license"]["contentIdStatus"] = content_id_status
    entry["license"]["evidenceFiles"] = copied
    _atomic_json(Path(catalog_path), catalog)

    local_meta = {
        "schemaVersion": 1, "assetId": asset_id, "title": entry["title"],
        "artist": entry["artist"], "source": entry["source"],
        "sourcePage": entry["sourcePage"], "artistPage": entry.get("artistPage"),
        "license": entry["license"],
        "registeredAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sha256": digest, **media, "evidenceFiles": copied,
    }
    _atomic_json(target.parent / "asset.json", local_meta)
    return {"entry": entry, "local": local_meta, "path": str(target)}


def catalog_status(*, repo_root: str | Path = REPO_ROOT,
                   catalog_path: str | Path = CATALOG_PATH,
                   schema_path: str | Path = CATALOG_SCHEMA_PATH) -> list[dict]:
    catalog = load_catalog(catalog_path, schema_path)
    return [inspect_entry(entry, repo_root) for entry in catalog["assets"]]


def _duration_label(seconds: float) -> str:
    total = max(0, round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def write_dashboard(out_path: str | Path = LOCAL_BGM_ROOT / "index.html", *,
                    repo_root: str | Path = REPO_ROOT,
                    catalog_path: str | Path = CATALOG_PATH,
                    schema_path: str | Path = CATALOG_SCHEMA_PATH) -> Path:
    """다운로드 진행도·공식 링크·증빙·로컬 audio player를 한 HTML에 기록한다."""
    out = Path(out_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(catalog_path, schema_path)
    rows = {row["id"]: row for row in catalog_status(
        repo_root=repo_root, catalog_path=catalog_path, schema_path=schema_path)}
    ready = sum(1 for row in rows.values() if row["ok"])
    total = len(rows)
    cards: list[str] = []
    for asset in catalog["assets"]:
        row = rows[asset["id"]]
        checks = row["checks"]
        is_ready = row["ok"]
        audio_path = Path(row["path"])
        rel_audio = Path(os.path.relpath(audio_path, out.parent)).as_posix()
        player = (f'<audio controls preload="metadata" src="{html.escape(rel_audio, quote=True)}"></audio>'
                  if checks["audio"] else
                  '<div class="audio-missing">MP3 등록 후 이 위치에 플레이어가 표시됩니다.</div>')
        evidence_links: list[str] = []
        for rel in asset.get("license", {}).get("evidenceFiles") or []:
            target = audio_path.parent / rel
            if target.is_file():
                link = Path(os.path.relpath(target, out.parent)).as_posix()
                evidence_links.append(
                    f'<a href="{html.escape(link, quote=True)}">{html.escape(Path(rel).name)}</a>')
        pills = "".join(
            f'<span class="pill {"pass" if checks.get(key) else "fail"}">{label} '
            f'{"PASS" if checks.get(key) else "WAIT"}</span>'
            for key, label in (("audio", "MP3"), ("evidence", "증빙"),
                               ("hash", "HASH"), ("metadata", "META"))
        )
        youtube_allowed = bool(asset.get("youtubeAllowed", False))
        pills = (
            f'<span class="pill {"pass" if youtube_allowed else "fail"}">'
            f'{"YT ALLOWED" if youtube_allowed else "YT BLOCKED"}</span>' + pills
        )
        detail_items = [*row["errors"], *row["warnings"]]
        details = "".join(f"<li>{html.escape(item)}</li>" for item in detail_items)
        tags = " · ".join(html.escape(tag) for tag in asset.get("tags", []))
        skills = ", ".join(html.escape(skill) for skill in asset.get("recommendedSkills", []))
        license_info = asset.get("license") or {}
        attribution = license_info.get("attributionText") if license_info.get("attributionRequired") else None
        attribution_block = (
            f'<div class="attribution"><strong>필수 저작자 표시</strong><pre>{html.escape(attribution)}</pre></div>'
            if isinstance(attribution, str) and attribution.strip() else ""
        )
        cards.append(f'''<article class="card {"ready" if is_ready else "pending"}">
  <div class="card-head"><div><div class="eyebrow">{html.escape(asset["id"])}</div>
  <h2>{html.escape(asset["title"])}</h2></div><strong>{_duration_label(asset["durationSec"])}</strong></div>
  <p class="artist">{html.escape(asset.get("artist") or "작가 확인 대기")} · {tags}</p>
  <div class="pills">{pills}</div>{player}
  <div class="actions"><a class="primary" href="{html.escape(asset["sourcePage"], quote=True)}" target="_blank" rel="noreferrer">공식 청취·다운로드</a>
  <a href="{html.escape(asset["license"]["url"], quote=True)}" target="_blank" rel="noreferrer">라이선스</a>
  {" · ".join(evidence_links)}</div>
  <p class="skills">라이선스: {html.escape(license_info.get("name") or "미확인")} · 추천: {skills}</p>
  {attribution_block}
  <details {"" if is_ready else "open"}><summary>검사 상세</summary><ul>{details or "<li>ready</li>"}</ul></details>
</article>''')

    percent = round(ready / total * 100) if total else 0
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BrushVid BGM Assets</title>
<style>
:root{{--paper:#f3efe6;--ink:#1d2421;--muted:#65706a;--card:#fffdf8;--line:#d9d3c8;--green:#285f48;--red:#a34838}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Noto Sans KR",sans-serif}}
main{{max-width:1180px;margin:auto;padding:52px 24px 80px}}h1{{font:700 42px/1.1 Georgia,serif;margin:8px 0 12px}}.intro{{color:var(--muted);max-width:760px}}
.progress{{margin:28px 0 36px;background:#ddd6c9;height:12px;border-radius:99px;overflow:hidden}}.progress i{{display:block;width:{percent}%;height:100%;background:var(--green)}}
.summary{{display:flex;gap:18px;align-items:end;justify-content:space-between}}.count{{font:700 30px/1 Georgia,serif}}code{{background:#e8e2d7;padding:3px 7px;border-radius:5px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:18px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 8px 30px #463d2b0d}}
.card.ready{{border-color:#8cb49f}}.card-head{{display:flex;gap:15px;justify-content:space-between}}h2{{font:700 24px/1.2 Georgia,serif;margin:4px 0}}.eyebrow{{font:11px/1.2 ui-monospace,monospace;color:var(--muted);overflow-wrap:anywhere}}
.artist,.skills{{color:var(--muted)}}.pills{{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0}}.pill{{font:700 10px ui-monospace,monospace;padding:5px 8px;border-radius:99px}}.pill.pass{{color:var(--green);background:#dfede5}}.pill.fail{{color:var(--red);background:#f3dfdb}}
audio{{width:100%;margin:7px 0 14px}}.audio-missing{{padding:15px;border:1px dashed var(--line);border-radius:10px;color:var(--muted);margin:7px 0 14px}}
.attribution{{margin-top:14px;padding:12px;border-radius:10px;background:#eee8dd}}.attribution pre{{white-space:pre-wrap;overflow-wrap:anywhere;margin:7px 0 0;font:12px/1.5 ui-monospace,monospace}}
.actions{{display:flex;flex-wrap:wrap;gap:8px;align-items:center}}a{{color:var(--green);font-weight:650;text-decoration:none}}a.primary{{background:var(--green);color:white;padding:9px 12px;border-radius:9px}}
details{{margin-top:13px;border-top:1px solid var(--line);padding-top:10px}}summary{{cursor:pointer;color:var(--muted)}}ul{{padding-left:20px;margin-bottom:0}}footer{{margin-top:36px;color:var(--muted);font-size:12px}}
@media(max-width:600px){{main{{padding:32px 16px}}h1{{font-size:34px}}.summary{{display:block}}}}
</style></head><body><main><div class="eyebrow">OFFLINE ASSET CONTROL</div><div class="summary"><div><h1>BGM 다운로드·청취 현황</h1>
<p class="intro"><strong>Pixabay 음원은 YouTube 일반 영상·Shorts 제작과 배포에 사용 금지입니다.</strong> Pixabay 자산은 로컬 청취·내부 데모·과거 검증용으로만 보존합니다. 공식 페이지에서 원래 파일명으로 청취·다운로드한 뒤 <code>bin/bgm-assets.py scan --attach</code>로 자동 연결하세요. 등록된 MP3는 이 파일에서 즉시 로컬 재생할 수 있습니다.</p></div>
<div class="count">{ready} / {total} ready</div></div><div class="progress"><i></i></div><section class="grid">{"".join(cards)}</section>
<footer>생성: {html.escape(created)} · Pixabay 음원은 YouTube/Shorts 배포 금지 · Content ID not-displayed는 미등록을 보장하지 않습니다. 공개 업로드 전에 다시 확인하세요.</footer>
</main></body></html>'''
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(document, encoding="utf-8")
    tmp.replace(out)
    return out


LISTENING_REVIEW_ITEMS = (
    ("gain-ab", "기존 음량 vs +4/+5/+6dB", "output/bgm-gain-ab-demo.mp4",
     "data/bgm-gain-ab-demo/audit/audit-report.md"),
    ("gentle-brush", "Gentle Piano · 일반 brush 30초", "output/ambient-local-bgm-demo.mp4",
     "data/ambient-local-bgm-demo/audit/audit-report.md"),
    ("dreamcloud-pen-brush", "Dreamcloud · pen-brush 30초",
     "output/pen-brush-local-bgm-demo.mp4",
     "data/pen-brush-local-bgm-demo/audit/audit-report.md"),
    ("quiet-narration", "Quiet Contemplation · 내레이션 덕킹 60초",
     "output/narration-local-bgm-demo.mp4",
     "data/narration-local-bgm-demo/audit/audit-report.md"),
    ("gentle-dreamcloud-playlist", "Gentle → Dreamcloud · 10분 playlist",
     "output/bgm-playlist-gentle-dreamcloud-610s.mp4",
     "data/bgm-playlist-gentle-dreamcloud-610s/audit/audit-report.md"),
)


def write_listening_review(out_path: str | Path = LOCAL_BGM_ROOT / "listening-review.html", *,
                           repo_root: str | Path = REPO_ROOT) -> Path:
    """사람 청취 승인에 필요한 영상·환경별 체크·JSON 내보내기를 한 페이지로 묶는다."""
    root = Path(repo_root).resolve()
    out = Path(out_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    for item_id, title, video_rel, audit_rel in LISTENING_REVIEW_ITEMS:
        video = root / video_rel
        audit = root / audit_rel
        video_href = Path(os.path.relpath(video, out.parent)).as_posix()
        audit_href = Path(os.path.relpath(audit, out.parent)).as_posix()
        available = video.is_file()
        player = (f'<video controls preload="metadata" src="{html.escape(video_href, quote=True)}"></video>'
                  if available else '<p class="missing">필수 검증 영상이 아직 없습니다. 생성 전에는 승인할 수 없습니다.</p>')
        audit_link = (f'<a href="{html.escape(audit_href, quote=True)}">audit report</a>'
                      if audit.is_file() else '<span class="muted">audit 대기</span>')
        disabled = "" if available else " disabled"
        cards.append(f'''<article class="review" data-id="{html.escape(item_id)}" data-available="{str(available).lower()}">
<h2>{html.escape(title)}</h2>{player}<div class="meta">{audit_link}</div>
<div class="checks"><label><input type="checkbox" data-field="earbuds"{disabled}> 이어폰 승인</label>
<label><input type="checkbox" data-field="laptop"{disabled}> 노트북 스피커 승인</label></div>
<label class="decision">판정 <select data-field="decision"{disabled}><option value="pending">대기</option>
<option value="pass">승인</option><option value="reject">수정 필요</option></select></label>
<textarea data-field="notes"{disabled} placeholder="음량, 펌핑, 전환, pen→brush 고조감 메모"></textarea>
</article>''')
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f'''<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BrushVid BGM Listening Review</title>
<style>:root{{--paper:#f1eee6;--card:#fffdf8;--ink:#202521;--muted:#68706b;--green:#285f48;--line:#d8d2c7}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Noto Sans KR",sans-serif}}
main{{max-width:1100px;margin:auto;padding:48px 22px 80px}}h1{{font:700 40px/1.15 Georgia,serif;margin:5px 0 12px}}.intro,.muted{{color:var(--muted)}}
.bar{{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 30px}}button,a.button{{border:0;border-radius:9px;padding:10px 14px;background:var(--green);color:white;font-weight:700;text-decoration:none;cursor:pointer}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px}}.review{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px}}
h2{{font:700 22px/1.2 Georgia,serif;margin:0 0 14px}}video{{width:100%;max-height:330px;background:#111;border-radius:10px}}.missing{{padding:40px;border:1px dashed var(--line);text-align:center;color:var(--muted)}}
.meta{{margin:8px 0 14px}}.meta a{{color:var(--green)}}.checks{{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}}.decision{{display:block;margin:10px 0}}select{{margin-left:8px;padding:6px}}
textarea{{width:100%;min-height:82px;border:1px solid var(--line);border-radius:9px;padding:10px;font:inherit}}.saved{{color:var(--green);font-weight:700}}
@media(max-width:600px){{.grid{{grid-template-columns:1fr}}main{{padding:30px 14px}}h1{{font-size:32px}}}}</style></head>
<body><main><div class="muted">HUMAN LISTENING GATE · {html.escape(created)}</div><h1>BGM 최종 청취 승인</h1>
<p class="intro">이어폰과 노트북 스피커에서 각각 확인하세요. 등록 음원은 카탈로그 대시보드에서 개별 청취하고, 필수 E2E 검증 영상은 여기서 최종 승인합니다.</p>
<div class="bar"><button id="export">승인 JSON 내보내기</button><button id="reset">입력 초기화</button><span id="saved" class="saved"></span></div>
<section class="grid">{"".join(cards)}</section></main>
<script>const KEY='brushvid-bgm-listening-review-v1';const cards=[...document.querySelectorAll('.review')];const exportBtn=document.getElementById('export');const resetBtn=document.getElementById('reset');const savedEl=document.getElementById('saved');
const collect=()=>Object.fromEntries(cards.map(c=>[c.dataset.id,Object.fromEntries([...c.querySelectorAll('[data-field]')].map(e=>[e.dataset.field,e.type==='checkbox'?e.checked:e.value]))]));
const apply=d=>cards.forEach(c=>Object.entries(d[c.dataset.id]||{{}}).forEach(([k,v])=>{{const e=c.querySelector(`[data-field="${{k}}"]`);if(e)e.type==='checkbox'?e.checked=!!v:e.value=v}}));
try{{apply(JSON.parse(localStorage.getItem(KEY)||'{{}}'))}}catch{{}};
document.addEventListener('input',()=>{{localStorage.setItem(KEY,JSON.stringify(collect()));savedEl.textContent='저장됨';setTimeout(()=>savedEl.textContent='',900)}});
exportBtn.onclick=()=>{{const payload={{schemaVersion:1,exportedAt:new Date().toISOString(),items:collect()}};const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{{type:'application/json'}}));a.download='bgm-listening-review.json';a.click();URL.revokeObjectURL(a.href)}};
resetBtn.onclick=()=>{{if(confirm('입력한 청취 결과를 초기화할까요?')){{localStorage.removeItem(KEY);location.reload()}}}};</script></body></html>'''
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(document, encoding="utf-8")
    tmp.replace(out)
    return out


def record_listening_review(source_file: str | Path,
                            out_path: str | Path = LOCAL_BGM_ROOT / "listening-approval.json", *,
                            repo_root: str | Path = REPO_ROOT) -> dict:
    """브라우저에서 내보낸 환경별 청취 결과가 모두 승인일 때 로컬 근거로 기록한다."""
    source = Path(source_file).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise BgmAssetError(f"청취 승인 JSON 파일 없음: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BgmAssetError(f"청취 승인 JSON 파싱 실패: {source}") from exc
    if data.get("schemaVersion") != 1 or not isinstance(data.get("items"), dict):
        raise BgmAssetError("청취 승인 JSON 계약 오류: schemaVersion=1, items 객체 필요")
    expected = {item[0] for item in LISTENING_REVIEW_ITEMS}
    actual = set(data["items"])
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "없음"
        extra = ", ".join(sorted(actual - expected)) or "없음"
        raise BgmAssetError(f"청취 항목 불일치: 누락={missing}; 추가={extra}")
    failures: list[str] = []
    clean_items: dict[str, dict] = {}
    video_by_id = {item_id: Path(repo_root).resolve() / video_rel
                   for item_id, _title, video_rel, _audit_rel in LISTENING_REVIEW_ITEMS}
    for item_id in sorted(expected):
        if not video_by_id[item_id].is_file():
            failures.append(f"{item_id}: 필수 검증 영상 없음: {video_by_id[item_id]}")
        item = data["items"].get(item_id)
        if not isinstance(item, dict):
            failures.append(f"{item_id}: 결과 객체 누락")
            continue
        earbuds, laptop = item.get("earbuds"), item.get("laptop")
        decision, notes = item.get("decision"), item.get("notes", "")
        if not isinstance(earbuds, bool) or not isinstance(laptop, bool):
            failures.append(f"{item_id}: 이어폰/노트북 체크 형식 오류")
            continue
        if decision not in ("pending", "pass", "reject") or not isinstance(notes, str):
            failures.append(f"{item_id}: 판정/메모 형식 오류")
            continue
        clean_items[item_id] = {
            "earbuds": earbuds, "laptop": laptop,
            "decision": decision, "notes": notes.strip(),
        }
        if not earbuds or not laptop or decision != "pass":
            failures.append(
                f"{item_id}: 최종 승인 아님(earbuds={earbuds}, laptop={laptop}, decision={decision})"
            )
    if failures:
        raise BgmAssetError("청취 승인 미완료:\n- " + "\n- ".join(failures))
    payload = {
        "schemaVersion": 1,
        "recordedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sourceExportedAt": data.get("exportedAt"),
        "sourceFile": str(source),
        "sourceSha256": sha256_file(source),
        "environmentGate": "earbuds-and-laptop-speakers",
        "approved": True,
        "items": clean_items,
    }
    _atomic_json(Path(out_path).expanduser().resolve(), payload)
    return payload


FINAL_BGM_E2E = (
    "ambient-local-bgm-demo",
    "pen-brush-local-bgm-demo",
    "narration-local-bgm-demo",
    "bgm-playlist-gentle-dreamcloud-610s",
)


def final_bgm_gate(*, repo_root: str | Path = REPO_ROOT,
                   catalog_path: str | Path = CATALOG_PATH,
                   schema_path: str | Path = CATALOG_SCHEMA_PATH,
                   out_path: str | Path = LOCAL_BGM_ROOT / "final-gate.json") -> dict:
    """카탈로그 전체·필수 E2E·라이선스·사람 청취를 한 번에 확인하는 최종 완료 게이트."""
    root = Path(repo_root).resolve()
    rows = catalog_status(repo_root=root, catalog_path=catalog_path, schema_path=schema_path)
    asset_checks = [{"id": row["id"], "passed": row["ok"],
                     "errors": row["errors"], "warnings": row["warnings"]}
                    for row in rows]
    e2e_checks: list[dict] = []
    for project_id in FINAL_BGM_E2E:
        video = root / "output" / f"{project_id}.mp4"
        audit = root / "data" / project_id / "audit" / "audit-report.json"
        manifest = root / "data" / project_id / "licenses" / "bgm-manifest.json"
        verdict = None
        fail_count = None
        error = None
        if audit.is_file():
            try:
                audit_data = json.loads(audit.read_text(encoding="utf-8"))
                verdict = audit_data.get("verdict")
                fail_count = sum(1 for issue in audit_data.get("issues", [])
                                 if issue.get("severity") == "FAIL")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                error = f"audit JSON 파싱 실패: {exc}"
        else:
            error = "audit report 없음"
        passed = bool(video.is_file() and manifest.is_file()
                      and verdict == "PASS" and fail_count == 0 and error is None)
        e2e_checks.append({
            "projectId": project_id, "passed": passed,
            "video": str(video), "videoExists": video.is_file(),
            "audit": str(audit), "verdict": verdict, "failCount": fail_count,
            "licenseManifest": str(manifest), "manifestExists": manifest.is_file(),
            "error": error,
        })

    approval_path = root / "local-assets" / "bgm" / "listening-approval.json"
    approval_ok = False
    approval_error = None
    if approval_path.is_file():
        try:
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            expected = {item[0] for item in LISTENING_REVIEW_ITEMS}
            items = approval.get("items") or {}
            approval_ok = bool(
                approval.get("approved") is True
                and set(items) == expected
                and all(item.get("earbuds") is True and item.get("laptop") is True
                        and item.get("decision") == "pass" for item in items.values())
            )
            if not approval_ok:
                approval_error = "승인 항목·환경·판정 계약 불충족"
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            approval_error = f"승인 JSON 파싱 실패: {exc}"
    else:
        approval_error = "listening-approval.json 없음"

    passed = all(item["passed"] for item in asset_checks) \
        and all(item["passed"] for item in e2e_checks) and approval_ok
    payload = {
        "schemaVersion": 1,
        "checkedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "passed": passed,
        "summary": {
            "assetsReady": sum(item["passed"] for item in asset_checks),
            "assetsTotal": len(asset_checks),
            "e2ePassed": sum(item["passed"] for item in e2e_checks),
            "e2eTotal": len(e2e_checks),
            "humanApproval": approval_ok,
        },
        "assets": asset_checks,
        "e2e": e2e_checks,
        "humanApproval": {
            "passed": approval_ok, "path": str(approval_path), "error": approval_error,
        },
    }
    _atomic_json(Path(out_path).expanduser().resolve(), payload)
    return payload


def discover_downloads(search_dirs: list[str | Path], *,
                       catalog_path: str | Path = CATALOG_PATH,
                       schema_path: str | Path = CATALOG_SCHEMA_PATH) -> list[dict]:
    """Pixabay 공식 URL의 마지막 slug와 로컬 파일명을 정확히 대조한다."""
    catalog = load_catalog(catalog_path, schema_path)
    # import 계약이 공식 MP3로 고정되어 있으므로 자동 연결 후보도 MP3만 본다.
    audio_exts = {".mp3"}
    files: list[Path] = []
    for directory in search_dirs:
        root = Path(directory).expanduser().resolve()
        if root.is_dir():
            files.extend(p for p in root.rglob("*")
                         if p.is_file() and p.suffix.lower() in audio_exts)

    def normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")

    results: list[dict] = []
    for asset in catalog["assets"]:
        if asset.get("downloaded"):
            continue
        slug = Path(urlparse(asset["sourcePage"]).path.rstrip("/")).name
        normalized_slug = normalized(slug)
        # Pixabay 곡 URL은 장르 prefix(`meditationspiritual-`)를 포함하지만 공식 MP3
        # 파일명에는 이 prefix가 빠지는 경우가 있다. 두 형태를 모두 정확한 suffix로만 허용한다.
        aliases = {normalized_slug}
        for prefix in ("meditationspiritual-",):
            if normalized_slug.startswith(prefix):
                aliases.add(normalized_slug[len(prefix):])
        candidates = []
        for path in files:
            stem = normalized(path.stem)
            # Pixabay 기본 파일명은 보통 `<작가>-<공식 slug>`다. 단순 부분 문자열은
            # `...-remix` 같은 다른 곡까지 잡을 수 있으므로 전체 일치 또는 suffix만 허용한다.
            if any(alias and (stem == alias or stem.endswith(f"-{alias}")) for alias in aliases):
                candidates.append(str(path))
        results.append({"id": asset["id"], "title": asset["title"],
                        "sourceSlug": slug, "slugAliases": sorted(aliases),
                        "candidates": sorted(candidates)})
    return results

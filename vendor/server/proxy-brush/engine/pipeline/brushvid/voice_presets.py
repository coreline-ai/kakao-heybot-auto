"""Supertonic 여성 음성팩 카탈로그와 결정적 style preset 해석기.

카탈로그가 음성 ID, 혼합 비율, 프리뷰와 라이선스 메타데이터의 단일 진실이다.
F1~F5는 female-01~female-05의 호환 별칭이며 M1~M5는 기존 native voice로
그대로 통과한다. 알 수 없는 ID를 F1로 조용히 대체하지 않는다.
"""
from __future__ import annotations

import hashlib
import html
import importlib.metadata
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np
from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "assets" / "voices" / "catalog.json"
CATALOG_SCHEMA_PATH = REPO_ROOT / "assets" / "voices" / "catalog.schema.json"
SPEED_MIN = 0.70
SPEED_MAX = 2.00


class VoicePresetError(ValueError):
    """음성팩 계약, style source 또는 preview asset 위반."""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json_sha256(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def load_catalog(path: str | Path = CATALOG_PATH,
                 schema_path: str | Path = CATALOG_SCHEMA_PATH) -> dict:
    """JSON Schema와 음성팩 의미 규칙을 모두 검증해 반환한다."""
    path, schema_path = Path(path), Path(schema_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VoicePresetError(f"음성 catalog를 읽을 수 없음: {exc}") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(data),
        key=lambda e: list(e.absolute_path),
    )
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.absolute_path) or "<root>"
        raise VoicePresetError(f"음성 catalog 검증 실패 {where}: {first.message}")

    expected_ids = [f"female-{i:02d}" for i in range(1, 11)]
    ids = [v["id"] for v in data["voices"]]
    if ids != expected_ids:
        raise VoicePresetError(f"음성 catalog ID/순서 오류: expected={expected_ids}, actual={ids}")
    if len(ids) != len(set(ids)):
        raise VoicePresetError("음성 catalog에 중복 voice ID가 있음")
    for voice in data["voices"]:
        components = voice["components"]
        if not math.isclose(sum(components.values()), 1.0, rel_tol=0, abs_tol=1e-8):
            raise VoicePresetError(f"{voice['id']} 구성 비율 합이 1.0이 아님: {components}")
        if any(not name.startswith("F") for name in components):
            raise VoicePresetError(f"{voice['id']} 여성팩에 남성/알 수 없는 component 포함: {components}")
        if voice["kind"] == "builtin" and (len(components) != 1 or 1.0 not in components.values()):
            raise VoicePresetError(f"{voice['id']} builtin은 단일 100% component여야 함")
        if voice["kind"] == "blend" and len(components) < 2:
            raise VoicePresetError(f"{voice['id']} blend는 2개 이상 component가 필요함")
        expected_preview = f"assets/voices/previews/{voice['id']}.mp3"
        if voice["preview"]["path"] != expected_preview:
            raise VoicePresetError(
                f"{voice['id']} preview 경로 불일치: {voice['preview']['path']}"
            )
    return data


def catalog_sha256(catalog: dict | None = None) -> str:
    return _json_sha256(catalog or load_catalog())


def voice_map(catalog: dict | None = None) -> dict[str, dict]:
    return {v["id"]: v for v in (catalog or load_catalog())["voices"]}


def supported_voice_ids(catalog: dict | None = None) -> tuple[str, ...]:
    catalog = catalog or load_catalog()
    return tuple([v["id"] for v in catalog["voices"]] + list(catalog["nativeVoices"]))


def resolve_voice(voice: str, catalog: dict | None = None) -> dict:
    """요청 ID를 canonical preset과 component 조합으로 해석한다."""
    catalog = catalog or load_catalog()
    if not isinstance(voice, str) or not voice.strip():
        raise VoicePresetError("input.tts.voice는 비어 있지 않은 문자열이어야 함")
    requested = voice.strip()
    canonical = catalog["aliases"].get(requested, requested)
    preset = voice_map(catalog).get(canonical)
    if preset is not None:
        return {
            "requestedVoice": requested,
            "voicePresetId": canonical,
            "kind": preset["kind"],
            "displayName": preset["displayName"],
            "components": dict(preset["components"]),
            "recommendedSpeed": preset["recommendedSpeed"],
            "voicePackVersion": catalog["voicePackVersion"],
            "catalogSha256": catalog_sha256(catalog),
            "aiDisclosure": catalog["aiDisclosure"],
            "license": dict(catalog["license"]),
        }
    # F1~F5는 위 alias로 항상 preset이 된다. native passthrough는 M1~M5만 남는다.
    if requested in catalog["nativeVoices"]:
        return {
            "requestedVoice": requested,
            "voicePresetId": requested,
            "kind": "native",
            "displayName": f"Supertonic {requested}",
            "components": {requested: 1.0},
            "recommendedSpeed": catalog["engine"]["defaultSpeed"],
            "voicePackVersion": catalog["voicePackVersion"],
            "catalogSha256": catalog_sha256(catalog),
            "aiDisclosure": catalog["aiDisclosure"],
            "license": dict(catalog["license"]),
        }
    allowed = ", ".join(supported_voice_ids(catalog))
    raise VoicePresetError(f"지원하지 않는 TTS voice: {requested!r} (허용: {allowed})")


def validate_voice_id(voice: str, catalog: dict | None = None) -> str:
    resolve_voice(voice, catalog)
    return voice.strip()


def _style_hash(style: Any) -> str:
    h = hashlib.sha256()
    for name in ("ttl", "dp"):
        arr = np.asarray(getattr(style, name), dtype=np.float32)
        h.update(name.encode())
        h.update(str(arr.shape).encode())
        h.update(arr.tobytes(order="C"))
    return h.hexdigest()


def _source_style_hash(tts: Any, name: str, style: Any, catalog: dict) -> tuple[str, str]:
    """모델의 원본 style JSON 해시를 우선 사용하고 없으면 배열 해시로 식별한다."""
    model_dir = getattr(tts, "model_dir", None)
    if model_dir is not None:
        source = Path(model_dir) / "voice_styles" / f"{name}.json"
        if not source.is_file():
            raise VoicePresetError(f"Supertonic 기본 style 파일 없음: {source}")
        actual = sha256_file(source)
        expected = catalog.get("baseStyleSha256", {}).get(name)
        if expected is not None and actual != expected:
            raise VoicePresetError(
                f"Supertonic {name} style drift: catalog={expected}, actual={actual}. "
                "모델/음성팩 버전을 확인하세요."
            )
        return actual, "style-json-sha256"
    return _style_hash(style), "style-array-sha256"


def build_voice_style(tts: Any, voice: str, *, catalog: dict | None = None,
                      style_factory: Callable[[np.ndarray, np.ndarray], Any] | None = None
                      ) -> tuple[Any, dict]:
    """Supertonic TTS 객체와 preset ID로 Style과 재현 메타데이터를 생성한다."""
    catalog = catalog or load_catalog()
    resolved = resolve_voice(voice, catalog)
    styles: dict[str, Any] = {}
    source_hashes: dict[str, str] = {}
    source_kinds: set[str] = set()
    for name in resolved["components"]:
        style = tts.get_voice_style(name)
        styles[name] = style
        digest, kind = _source_style_hash(tts, name, style, catalog)
        source_hashes[name] = digest
        source_kinds.add(kind)

    components = resolved["components"]
    if len(components) == 1 and next(iter(components.values())) == 1.0:
        style = styles[next(iter(components))]
    else:
        ttl = sum(np.asarray(styles[name].ttl, dtype=np.float32) * weight
                  for name, weight in components.items())
        dp = sum(np.asarray(styles[name].dp, dtype=np.float32) * weight
                 for name, weight in components.items())
        if style_factory is None:
            try:
                from supertonic import Style
            except ImportError as exc:  # pragma: no cover - tts.py가 설치 안내를 담당
                raise VoicePresetError("Supertonic Style을 불러올 수 없음") from exc
            style_factory = Style
        style = style_factory(np.asarray(ttl, dtype=np.float32), np.asarray(dp, dtype=np.float32))

    metadata = {
        **resolved,
        "styleSourceSha256": source_hashes,
        "styleSourceKind": next(iter(source_kinds)) if len(source_kinds) == 1 else "mixed",
        "styleSha256": _style_hash(style),
    }
    return style, metadata


def signature_material(voice: str, *, catalog: dict | None = None) -> dict:
    """모델 로드 없이 stt cache signature에 포함할 결정적 preset 자료."""
    catalog = catalog or load_catalog()
    resolved = resolve_voice(voice, catalog)
    configured_root = os.environ.get("DRAW_PROXY_SUPERTONIC_ROOT")
    cached_styles = (
        Path(configured_root).expanduser().resolve() / "voice_styles"
        if configured_root
        else Path.home() / ".cache" / catalog["engine"]["model"] / "voice_styles"
    )
    actual_style_hashes = {}
    for name in resolved["components"]:
        path = cached_styles / f"{name}.json"
        actual_style_hashes[name] = sha256_file(path) if path.is_file() else None
    try:
        installed_package = importlib.metadata.version("supertonic")
    except importlib.metadata.PackageNotFoundError:
        installed_package = "missing"
    return {
        "requestedVoice": resolved["requestedVoice"],
        "voicePresetId": resolved["voicePresetId"],
        "components": resolved["components"],
        "voicePackVersion": resolved["voicePackVersion"],
        "catalogSha256": resolved["catalogSha256"],
        "expectedStyleSha256": {
            name: catalog.get("baseStyleSha256", {}).get(name)
            for name in resolved["components"]
        },
        "actualStyleSha256": actual_style_hashes,
        "packageVersion": catalog["engine"]["packageVersion"],
        "installedPackageVersion": installed_package,
        "model": catalog["engine"]["model"],
    }


def tts_signature(text: str, tts_config: dict, *, catalog: dict | None = None) -> str:
    """대본과 TTS 설정을 StageLedger cache key로 변환한다."""
    voice = tts_config.get("voice", "F1")
    payload = {
        "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "engine": tts_config.get("engine", "supertonic"),
        "voice": signature_material(voice, catalog=catalog),
        "speed": tts_config.get("speed", 1.05),
        "pauseMs": tts_config.get("pauseMs", 300),
        "timing": tts_config.get("timing", "tts"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def preview_path(voice: str, *, catalog: dict | None = None,
                 repo_root: str | Path = REPO_ROOT) -> Path:
    catalog = catalog or load_catalog()
    resolved = resolve_voice(voice, catalog)
    preset = voice_map(catalog).get(resolved["voicePresetId"])
    if preset is None:
        raise VoicePresetError(f"{voice}는 프로젝트 여성팩 preview가 없는 native voice임")
    path = (Path(repo_root) / preset["preview"]["path"]).resolve()
    root = (Path(repo_root) / "assets" / "voices" / "previews").resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise VoicePresetError(f"preview가 허용 디렉터리 밖을 가리킴: {path}") from exc
    return path


def _probe_audio(path: Path) -> dict:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        )
        data = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise VoicePresetError(f"preview를 읽을 수 없음: {path}") from exc
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if stream is None:
        raise VoicePresetError(f"preview audio stream 없음: {path}")
    return {
        "codec": stream.get("codec_name"),
        "sampleRate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "durationSec": float(data.get("format", {}).get("duration") or stream.get("duration") or 0),
    }


def _decoded_acoustics(path: Path) -> dict:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "1", "-ar", "44100", "pipe:1"],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise VoicePresetError(f"preview decode 실패: {path}") from exc
    audio = np.frombuffer(result.stdout, dtype="<f4")
    if not len(audio):
        raise VoicePresetError(f"preview decode 결과가 비어 있음: {path}")
    peak = float(np.max(np.abs(audio)))
    active = np.abs(audio) > 0.002
    active_rms = float(np.sqrt(np.mean(np.square(audio[active])))) if np.any(active) else 0.0
    dbfs = lambda v: 20.0 * math.log10(max(v, 1e-12))
    return {
        "peakDbfs": dbfs(peak),
        "activeRmsDbfs": dbfs(active_rms),
        "clippedSamples": int(np.count_nonzero(np.abs(audio) >= 0.99999)),
    }


def validate_preview_assets(*, catalog: dict | None = None,
                            repo_root: str | Path = REPO_ROOT) -> list[dict]:
    """10개 preview의 파일·해시·포맷·길이·클리핑을 hard gate로 검사한다."""
    catalog = catalog or load_catalog()
    rows: list[dict] = []
    errors: list[str] = []
    for preset in catalog["voices"]:
        path = preview_path(preset["id"], catalog=catalog, repo_root=repo_root)
        item_errors: list[str] = []
        if not path.is_file() or path.stat().st_size == 0:
            item_errors.append(f"파일 없음: {path}")
        else:
            actual_hash = sha256_file(path)
            if actual_hash != preset["preview"]["sha256"]:
                item_errors.append(
                    f"SHA-256 불일치 catalog={preset['preview']['sha256']} actual={actual_hash}"
                )
            media = _probe_audio(path)
            for key in ("codec", "sampleRate", "channels"):
                if media[key] != preset["preview"][key]:
                    item_errors.append(
                        f"{key} 불일치 catalog={preset['preview'][key]} actual={media[key]}"
                    )
            if abs(media["durationSec"] - preset["preview"]["durationSec"]) > 0.02:
                item_errors.append(
                    f"duration 불일치 catalog={preset['preview']['durationSec']} "
                    f"actual={media['durationSec']:.3f}"
                )
            acoustics = _decoded_acoustics(path)
            if acoustics["clippedSamples"]:
                item_errors.append(f"clipped samples={acoustics['clippedSamples']}")
            if acoustics["peakDbfs"] > -1.0:
                item_errors.append(f"peak 과다={acoustics['peakDbfs']:.2f}dBFS")
            if not -24.0 <= acoustics["activeRmsDbfs"] <= -16.0:
                item_errors.append(f"active RMS 범위 밖={acoustics['activeRmsDbfs']:.2f}dBFS")
            media.update(acoustics)
        rows.append({"id": preset["id"], "path": str(path), "errors": item_errors,
                     "ok": not item_errors})
        errors.extend(f"{preset['id']}: {msg}" for msg in item_errors)
    if errors:
        raise VoicePresetError("음성 preview 검증 실패\n- " + "\n- ".join(errors))
    return rows


def write_catalog_html(out_path: str | Path | None = None, *, catalog: dict | None = None,
                       repo_root: str | Path = REPO_ROOT) -> Path:
    """저장소 MP3를 즉시 재생하는 자체 포함 음성팩 청취 페이지를 만든다."""
    catalog = catalog or load_catalog()
    out = Path(out_path) if out_path else Path(repo_root) / "assets" / "voices" / "index.html"
    out = out.expanduser().resolve()
    cards = []
    rows = []
    for preset in catalog["voices"]:
        audio = preview_path(preset["id"], catalog=catalog, repo_root=repo_root)
        try:
            src = audio.relative_to(out.parent).as_posix()
        except ValueError:
            src = audio.as_uri()
        mix = " + ".join(f"{name} {weight:.0%}" for name, weight in preset["components"].items())
        uses = " · ".join(preset["useCases"])
        cards.append(f"""
        <article class="voice-card" id="{preset['id']}">
          <div class="number">{preset['id'][-2:]}</div>
          <div class="voice-main">
            <div class="title-row"><h2>{html.escape(preset['displayName'])}</h2><span>{html.escape(preset['badge'])}</span></div>
            <p class="metrics"><code>{preset['id']}</code> · {html.escape(mix)} · {html.escape(preset['pitch'])} {preset['pitchHz']}Hz · {html.escape(preset['pace'])}</p>
            <p>{html.escape(preset['summary'])}</p>
            <p class="use"><b>추천:</b> {html.escape(uses)}</p>
            <audio controls preload="metadata" src="{html.escape(src)}"></audio>
          </div>
        </article>""")
        rows.append(
            f"<tr><th>{preset['id']}</th><td>{html.escape(preset['displayName'])}</td>"
            f"<td>{html.escape(mix)}</td><td>{html.escape(uses)}</td></tr>"
        )
    content = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Supertonic 여성 음성팩 {catalog['voicePackVersion']}</title>
<style>
:root{{--ink:#172238;--navy:#263b68;--gold:#c8922d;--paper:#f5f0e7;--line:#d9cfbe}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#faf7f1,#eee7dc);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}}
header{{padding:48px max(24px,6vw) 34px;background:#172238;color:white;border-bottom:5px solid var(--gold)}}h1{{margin:0 0 12px;font-size:clamp(30px,5vw,56px)}}header p{{max-width:900px;line-height:1.7;color:#dbe2ef}}
.toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}}button{{border:0;border-radius:999px;padding:11px 17px;background:var(--gold);color:#172238;font-weight:850;cursor:pointer}}
main{{width:min(1160px,92vw);margin:30px auto 60px}}.notice{{background:#fffaf1;border:1px solid var(--line);padding:16px 20px;border-radius:16px;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}}.voice-card{{display:flex;gap:16px;background:white;border:1px solid var(--line);border-radius:19px;padding:20px;box-shadow:0 8px 26px #3a302118}}
.number{{display:grid;place-items:center;flex:0 0 46px;height:46px;border-radius:14px;background:#e8dfcf;color:var(--navy);font-weight:900}}.voice-main{{min-width:0;flex:1}}.title-row{{display:flex;gap:9px;align-items:center;flex-wrap:wrap}}h2{{font-size:21px;margin:2px 0 5px}}.title-row span{{background:#f5e6bd;color:#62450b;padding:4px 8px;border-radius:999px;font-size:12px;font-weight:800}}p{{line-height:1.6}}.metrics{{color:#687181;font-size:13px}}.use{{background:#f3f5f9;padding:8px 10px;border-radius:9px;font-size:13px}}audio{{width:100%}}
table{{width:100%;border-collapse:collapse;background:white;margin:28px 0;border-radius:16px;overflow:hidden}}th,td{{padding:13px;border-bottom:1px solid #ece5da;text-align:left}}thead th{{background:#202e4a;color:white}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}table{{display:block;overflow:auto}}}}
</style></head><body>
<header><h1>Supertonic 여성 음성팩 v{catalog['voicePackVersion']}</h1><p>공식 F1~F5와 여성 style vector만 사용한 결정적 혼합 5종입니다. 프로젝트에서는 <code>female-01</code>~<code>female-10</code>을 사용합니다.</p><div class="toolbar"><button id="playAll">10종 순서대로 듣기</button><button id="stopAll">모두 정지</button></div></header>
<main><section class="notice"><b>AI 음성 고지</b><br>{html.escape(catalog['aiDisclosure'])}</section>
<table><thead><tr><th>ID</th><th>이름</th><th>구성</th><th>추천</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<section class="grid">{''.join(cards)}</section></main>
<script>const a=[...document.querySelectorAll('audio')];document.getElementById('playAll').onclick=()=>{{let i=0;a.forEach(x=>{{x.pause();x.currentTime=0}});const n=()=>{{if(i>=a.length)return;const x=a[i++];x.onended=n;x.play()}};n()}};document.getElementById('stopAll').onclick=()=>a.forEach(x=>{{x.pause();x.currentTime=0}});</script>
</body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out

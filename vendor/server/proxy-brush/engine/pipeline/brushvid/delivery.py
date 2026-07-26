"""최종 영상 업로드용 제목·설명·저작자 표시 산출물."""
from __future__ import annotations

import json
import hashlib
import shutil
from datetime import datetime
from pathlib import Path


def write_delivery_package(out_dir: str | Path, *, project_id: str, video: str | Path,
                           title: str, asset: dict | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    video_path = Path(video).resolve()
    attribution = ""
    if asset is not None:
        license_info = asset.get("license") or {}
        attribution = str(license_info.get("attributionText") or "").strip()
        if license_info.get("attributionRequired") and not attribution:
            raise ValueError("저작자 표시 필수 음원의 attributionText가 비어 있음")

    description_lines = [title, ""]
    if attribution:
        description_lines += ["음악 출처 / Music Credit", "", attribution, ""]
    description = "\n".join(description_lines).rstrip() + "\n"
    upload_notes = (
        "YouTube Studio > 세부정보 > 설명에 youtube-description.txt 내용을 붙여 넣으세요.\n"
        "영상 전체를 CC BY로 재배포할 의도가 없다면 업로드의 라이선스 설정은 "
        "표준 YouTube 라이선스로 유지하세요.\n"
        "MP4 metadata는 설명란 저작자 표시를 대신하지 않습니다.\n"
    )

    (out / "youtube-title.txt").write_text(title + "\n", encoding="utf-8")
    (out / "youtube-description.txt").write_text(description, encoding="utf-8")
    (out / "ATTRIBUTION.txt").write_text((attribution + "\n") if attribution else "", encoding="utf-8")
    (out / "UPLOAD-NOTES.txt").write_text(upload_notes, encoding="utf-8")
    manifest = {
        "schemaVersion": 1,
        "projectId": project_id,
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "video": str(video_path),
        "title": title,
        "assetId": asset.get("id") if asset else None,
        "files": ["youtube-title.txt", "youtube-description.txt", "ATTRIBUTION.txt", "UPLOAD-NOTES.txt"],
    }
    manifest_path = out / "delivery-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"directory": str(out), "manifest": str(manifest_path), **manifest}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_final_delivery(out_dir: str | Path, *, project_id: str, video: str | Path,
                           project_yaml: str | Path, source_manifest: str | Path,
                           qa_dir: str | Path, audit_dir: str | Path,
                           license_manifest: str | Path | None, mix_report: str | Path) -> dict:
    """PASS audit을 확인한 뒤 외부 납품 폴더를 원자적으로 구성한다.

    기존 납품물을 덮어쓰지 않는다. 재납품은 별도 검토 후 사용자가 기존 폴더를
    보존/정리한 다음 실행해야 하며, 이 정책으로 검증 완료본을 실수로 교체하지 않는다.
    """
    output = Path(out_dir).resolve()
    video_path = Path(video).resolve()
    yaml_path = Path(project_yaml).resolve()
    source_path = Path(source_manifest).resolve()
    qa_path = Path(qa_dir).resolve()
    audit_path = Path(audit_dir).resolve()
    license_path = Path(license_manifest).resolve() if license_manifest else None
    mix_path = Path(mix_report).resolve()
    required_files = (video_path, yaml_path, source_path, mix_path,
                      audit_path / "audit-report.json", qa_path / "capture-manifest.json")
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        raise FileNotFoundError("납품 필수 파일 없음: " + ", ".join(missing))
    if not qa_path.is_dir() or not audit_path.is_dir():
        raise FileNotFoundError("QA/audit 디렉터리 없음")
    audit = json.loads((audit_path / "audit-report.json").read_text(encoding="utf-8"))
    summary = audit.get("summary") or {}
    spec = audit.get("spec") or {}
    if audit.get("verdict") != "PASS" or int(summary.get("FAIL", 1)) != 0:
        raise ValueError("audit PASS/FAIL 0 조건을 만족하지 않음")
    if (spec.get("width"), spec.get("height"), spec.get("fps")) != (3840, 2160, 30.0) \
            or spec.get("vcodec") != "h264" or spec.get("acodec") != "aac" \
            or abs(float(spec.get("duration", 0)) - 600.0) > 0.05 \
            or int(spec.get("nbFrames") or 0) != 18000:
        raise ValueError("최종 규격이 3840×2160/30fps/H.264/AAC/600초/18000프레임이 아님")
    mix = json.loads(mix_path.read_text(encoding="utf-8"))
    silent_delivery = (
        mix.get("mode") == "off" and mix.get("bgm") is None and mix.get("voice") is None
        and bool((mix.get("settings") or {}).get("silentAudioTrack"))
    )
    licensed_delivery = mix.get("mode") in {"asset", "playlist"}
    if not (licensed_delivery or silent_delivery):
        raise ValueError("최종 납품은 licensed BGM 또는 명시적 무음 AAC mix report가 필요함")
    if licensed_delivery:
        if license_path is None or not license_path.is_file():
            raise FileNotFoundError("licensed BGM 납품에는 BGM license manifest가 필요함")
        license_data = json.loads(license_path.read_text(encoding="utf-8"))
        if license_data.get("licensePolicy") != "strict" or not license_data.get("assets"):
            raise ValueError("strict BGM license manifest가 필요함")
    elif license_path is not None and license_path.exists():
        raise ValueError("BGM off 납품에는 BGM license manifest를 포함할 수 없음")
    if output.exists():
        raise FileExistsError(f"기존 납품 폴더를 덮어쓸 수 없음: {output}")
    staging = output.parent / f".{project_id}.staging"
    if staging.exists():
        raise FileExistsError(f"이전 납품 staging 폴더가 남아 있음: {staging}")
    staging.mkdir(parents=True)
    try:
        shutil.copy2(video_path, staging / f"{project_id}.mp4")
        shutil.copy2(yaml_path, staging / "project.yaml")
        shutil.copy2(source_path, staging / "source-manifest.json")
        if licensed_delivery:
            shutil.copy2(license_path, staging / "bgm-license-manifest.json")
        shutil.copy2(mix_path, staging / "mix-report.json")
        shutil.copytree(qa_path, staging / "qa")
        shutil.copytree(audit_path, staging / "audit")
        manifest = {
            "schemaVersion": 1,
            "projectId": project_id,
            "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "video": {
                "path": f"{project_id}.mp4", "sha256": _sha256_file(staging / f"{project_id}.mp4"),
                "width": 3840, "height": 2160, "fps": 30, "durationSec": 600,
                "vcodec": "h264", "acodec": "aac",
            },
            "audit": {"verdict": audit["verdict"], "summary": summary},
            "audio": {"mode": "off", "silentTrack": True} if silent_delivery else {
                "mode": mix.get("mode"), "licenseManifest": "bgm-license-manifest.json"},
            "files": ["project.yaml", "source-manifest.json", "mix-report.json", "qa/", "audit/"]
                     + (["bgm-license-manifest.json"] if licensed_delivery else []),
        }
        (staging / "delivery-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary_text = "\n".join([
            f"# {project_id} delivery summary",
            "",
            "- Status: PASS",
            "- Video: 3840×2160, 30fps, H.264/AAC, 600 seconds",
            f"- SHA-256: {manifest['video']['sha256']}",
            f"- Audit: FAIL {summary.get('FAIL', 0)} / WARN {summary.get('WARN', 0)} / INFO {summary.get('INFO', 0)}",
            "- Audio: intentional silent AAC track; no BGM" if silent_delivery
            else "- BGM: strict license manifest and mix report included",
            "",
        ])
        (staging / "generation-summary.md").write_text(summary_text, encoding="utf-8")
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"directory": str(output), "manifest": str(output / "delivery-manifest.json"),
            "videoSha256": manifest["video"]["sha256"]}

#!/usr/bin/env python3
"""대사 없는 완성 MP4의 BGM만 공용 엔진으로 교체한다.

영상은 stream-copy하고 외부 음원은 catalog/preflight/license/mix/audit 계약을 그대로 사용한다.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "pipeline" / ".venv" / "bin" / "python"
try:
    from brushvid import bgm as bv_bgm
    from brushvid import mix as bv_mix
    from brushvid.audit import run_audit
    from brushvid.delivery import write_delivery_package
    from brushvid.project import BgmConfig
    from brushvid.render import probe_duration
except ImportError:  # pragma: no cover
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        import os
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise

log = logging.getLogger("replace-bgm")


def video_stream_hash(path: str | Path) -> str:
    res = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c:v", "copy",
         "-f", "hash", "-hash", "sha256", "-"],
        capture_output=True, text=True, check=True,
    )
    return res.stdout.strip().split("=", 1)[-1]


def mux_replacement(video: str | Path, audio: str | Path, out: str | Path, *,
                    duration_sec: float, title: str, asset: dict) -> Path:
    output = Path(out).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    license_info = asset.get("license") or {}
    comment = str(license_info.get("attributionText") or "").replace("\n", " | ")
    cmd = [
        "ffmpeg", "-y", "-v", "warning", "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
        "-ar", "48000", "-ac", "2", "-t", f"{duration_sec:.6f}", "-movflags", "+faststart",
        "-metadata", f"title={title}",
        "-metadata", f"artist=Music: {asset.get('artist', '')}",
        "-metadata", f"copyright={license_info.get('name', '')}",
        "-metadata", f"comment={comment}",
        "-metadata:s:a:0", f"title={asset.get('title', '')}",
        "-metadata:s:a:0", f"artist={asset.get('artist', '')}",
        str(output),
    ]
    subprocess.run(cmd, check=True)
    return output


def main() -> int:
    ap = argparse.ArgumentParser(description="대사 없는 완성 MP4의 BGM을 catalog asset으로 교체")
    ap.add_argument("--video", required=True, help="기존 완성 MP4")
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--asset-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", required=True, help="공개용 영상 제목")
    ap.add_argument("--props", help="완료/경계 정밀 감사를 위한 props.json")
    ap.add_argument("--gain-db", type=float, default=5.0)
    ap.add_argument("--source-start", type=float, default=0.0,
                    help="원본 BGM 시작에서 건너뛸 초")
    ap.add_argument("--fade-in", type=float, default=1.8)
    ap.add_argument("--fade-out", type=float, default=2.0)
    ap.add_argument("--confirm-no-voice", action="store_true",
                    help="기존 오디오에 보존할 내레이션이 없음을 확인")
    a = ap.parse_args()
    if not a.confirm_no_voice:
        raise SystemExit("대사 없는 영상만 지원합니다. --confirm-no-voice로 확인하세요.")

    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")
    video = Path(a.video).expanduser().resolve()
    if not video.is_file():
        raise SystemExit(f"영상 없음: {video}")
    duration = probe_duration(video)
    if not 0.0 <= a.source_start <= 60.0:
        raise SystemExit("--source-start는 0~60초여야 합니다.")
    cfg = BgmConfig(mode="asset", asset_id=a.asset_id, gain_db=a.gain_db,
                    source_start_sec=a.source_start,
                    fade_in_sec=a.fade_in, fade_out_sec=a.fade_out, license_policy="strict")
    assets = bv_bgm.preflight_assets(cfg, distribution="youtube", repo_root=REPO_ROOT)
    asset = assets[0]

    data_dir = REPO_ROOT / "data" / a.project_id
    audio_dir = data_dir / "audio" / "replace-bgm"
    master, bgm_report = bv_mix.prepare_bgm(
        [asset["resolvedPath"]], audio_dir / "bgm-master.wav", duration_sec=duration,
        work_dir=audio_dir / "work", gain_db=a.gain_db,
        fade_in_sec=a.fade_in, fade_out_sec=a.fade_out,
        source_start_sec=a.source_start,
    )
    license_manifest = bv_bgm.write_license_manifest(
        data_dir / "licenses" / "bgm-manifest.json", a.project_id, cfg, assets,
        distribution="youtube")
    mix_report = bv_mix.write_mix_report(audio_dir / "mix-report.json", {
        "projectId": a.project_id, "durationSec": duration, "mode": "asset",
        "bgm": bgm_report, "voice": None,
        "settings": {"gainDb": a.gain_db, "sourceStartSec": a.source_start,
                     "fadeInSec": a.fade_in,
                     "fadeOutSec": a.fade_out, "crossfadeSec": 0.0},
    })

    before_hash = video_stream_hash(video)
    output = mux_replacement(video, master, a.out, duration_sec=duration,
                             title=a.title, asset=asset)
    after_hash = video_stream_hash(output)
    if before_hash != after_hash:
        output.unlink(missing_ok=True)
        raise SystemExit("영상 stream hash 불일치 — 재인코딩 방지 계약 실패")

    audit_dir = data_dir / "audit-bgm-replacement"
    audit = run_audit(output, props=a.props, out_dir=audit_dir,
                      license_manifest=license_manifest, mix_report=mix_report)
    if audit["verdict"] == "FAIL":
        raise SystemExit(f"최종 audit FAIL — {audit_dir / 'audit-report.md'}")
    delivery = write_delivery_package(data_dir / "delivery", project_id=a.project_id,
                                      video=output, title=a.title, asset=asset)
    result = {
        "video": str(output), "durationSec": duration, "assetId": a.asset_id,
        "videoStreamSha256": before_hash, "licenseManifest": str(license_manifest),
        "mixReport": str(mix_report), "audit": str(audit_dir / "audit-report.md"),
        "delivery": delivery["directory"],
    }
    (data_dir / "bgm-replacement-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

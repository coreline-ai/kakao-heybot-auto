#!/usr/bin/env python3
"""bin/audit.py — 완성 mp4 독립 검수 CLI.

사용:
  pipeline/.venv/bin/python bin/audit.py output/foo.mp4 [--props data/foo/props.json] [--out data/audit/foo]

산출: {out}/audit-report.md + audit-report.json + evidence/*.png (+ FIELD-LOG 초안 스니펫)
exit code: 0 = PASS / 1 = FAIL
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "pipeline" / ".venv" / "bin" / "python"

# pipeline venv 밖에서 실행되면 venv 파이썬으로 재실행 (bin/build.py 와 동일 규약)
try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        import os
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise SystemExit("numpy 미설치 — pipeline/README.md 부트스트랩을 먼저 실행하세요")

sys.path.insert(0, str(REPO_ROOT / "pipeline"))
from brushvid.audit import fmt_ts, run_audit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="완성 mp4 결함 자동 검출 (독립 검수기)")
    ap.add_argument("video", help="검수할 mp4 경로")
    ap.add_argument("--props", default=None, help="render-props JSON (씬 경계 정확 판정용, 선택)")
    ap.add_argument("--out", default=None, help="리포트 산출 디렉토리 (기본: data/audit/{영상이름})")
    ap.add_argument("--license-manifest", default=None,
                    help="BGM 라이선스 매니페스트 JSON (외부 BGM 사용 시)")
    ap.add_argument("--mix-report", default=None, help="BGM mix-report.json (선택)")
    ap.add_argument("--voice-manifest", default=None,
                    help="TTS voice-manifest.json (합성 음성 재현성·AI 고지 검사)")
    ap.add_argument("--no-evidence", action="store_true", help="증거 스틸 PNG 생략")
    a = ap.parse_args()

    video = Path(a.video)
    if not video.is_file():
        print(f"파일 없음: {video}", file=sys.stderr)
        return 2
    out_dir = Path(a.out) if a.out else REPO_ROOT / "data" / "audit" / video.stem

    result = run_audit(video, props=a.props, out_dir=out_dir, evidence=not a.no_evidence,
                       license_manifest=a.license_manifest, mix_report=a.mix_report,
                       voice_manifest=a.voice_manifest)

    print(f"[{result['verdict']}] {video.name} — FAIL {result['summary']['FAIL']} / "
          f"WARN {result['summary']['WARN']} / INFO {result['summary']['INFO']} "
          f"(스캔 {result['stats']['totalSec']}s, 경계 {result['boundarySource']} "
          f"{result['boundaryCount']}개)")
    for it in result["issues"]:
        fr = f"f{it['frame']}" if it["frame"] is not None else "-"
        print(f"  {it['severity']:4s} {it['kind']:17s} {fr:>8s} {fmt_ts(it['timeSec']):>7s}  {it['message']}")
    print(f"리포트: {out_dir / 'audit-report.md'}")
    return 1 if result["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())

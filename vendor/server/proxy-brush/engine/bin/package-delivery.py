#!/usr/bin/env python3
"""audit PASS된 UHD 프로젝트를 imagegen_remotion/video_output으로 납품한다."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "pipeline" / ".venv" / "bin" / "python"

try:
    import brushvid  # noqa: F401
except ImportError:  # pragma: no cover
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        import os
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise

from brushvid.delivery import package_final_delivery
from brushvid.project import load_project

DEFAULT_ROOT = Path("output/delivery")


def main() -> int:
    ap = argparse.ArgumentParser(description="UHD audit PASS 프로젝트 납품 패키지 생성")
    ap.add_argument("project_yaml", help="검증 완료 project.yaml")
    ap.add_argument("--delivery-root", default=str(DEFAULT_ROOT))
    args = ap.parse_args()
    cfg = load_project(args.project_yaml)
    result = package_final_delivery(
        Path(args.delivery_root) / cfg.project_id,
        project_id=cfg.project_id,
        video=REPO_ROOT / "output" / f"{cfg.project_id}.mp4",
        project_yaml=args.project_yaml,
        source_manifest=cfg.base_dir / "source-manifest.json",
        qa_dir=REPO_ROOT / "data" / cfg.project_id / "qa",
        audit_dir=REPO_ROOT / "data" / cfg.project_id / "audit",
        license_manifest=(REPO_ROOT / "data" / cfg.project_id / "licenses" / "bgm-manifest.json")
        if cfg.bgm and cfg.bgm.mode in {"asset", "playlist"} else None,
        mix_report=REPO_ROOT / "data" / cfg.project_id / "audio" / "mix-report.json",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""bin/qa.py — QA 단독 실행: 씬별 프레임 캡처 + capture-manifest.json + 콘택트시트.

빌드가 끝난 프로젝트(data/{pid}/props.json + scenes.json)에 대해 QA 산출물만 다시 만든다.

사용:
  pipeline/.venv/bin/python bin/qa.py examples/ambient/project.yaml
  pipeline/.venv/bin/python bin/qa.py examples/ambient/project.yaml --frames 0,150,299
"""
from __future__ import annotations

import argparse
import logging
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
    raise SystemExit("brushvid 미설치 — pipeline/README.md 의 부트스트랩 절차를 먼저 실행하세요")

import importlib.util

from brushvid import qa as bv_qa
from brushvid.project import load_project

# bin/build.py 의 Pipeline 재사용 (경로 규약/씬별 캡처 로직 단일 소스)
_spec = importlib.util.spec_from_file_location("buildmod", Path(__file__).with_name("build.py"))
buildmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(buildmod)

log = logging.getLogger("qa")


def main() -> None:
    ap = argparse.ArgumentParser(description="빌드 완료 프로젝트의 QA 산출물 재생성")
    ap.add_argument("project_yaml", help="project.yaml 경로")
    ap.add_argument("--frames", default=None, help="캡처 프레임 목록 (예: 0,150,299) — 미지정 시 씬별 start/mid/end")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")

    cfg = load_project(a.project_yaml)
    pipe = buildmod.Pipeline(cfg)
    if not pipe.props_json.is_file():
        raise SystemExit(f"props 없음: {pipe.props_json} — 먼저 bin/build.py 를 실행하세요")

    qa_dir = pipe.data_dir / "qa"
    if a.frames:
        frames = [int(f) for f in a.frames.split(",")]
        composition = buildmod.resolve_composition(cfg)
        entries = bv_qa.capture_frames(pipe.props_json, frames, qa_dir, composition=composition)
        bv_qa.write_manifest(entries, qa_dir, project_id=cfg.project_id, props=str(pipe.props_json))
        sheet = bv_qa.contact_sheet(qa_dir, cols=3)
    else:
        result = pipe.stage_qa()
        sheet = result["contactSheet"]
    gallery = bv_qa.build_gallery(pipe.props_json, qa_dir)  # 씬 갤러리(카드뷰)도 기본 생성
    log.info("QA 완료 → %s / %s", sheet, gallery)


if __name__ == "__main__":
    main()

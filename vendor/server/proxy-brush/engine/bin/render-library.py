#!/usr/bin/env python3
"""Render one project from the imported new-video-gen asset library."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from brushvid.render import render  # noqa: E402


def load_library() -> dict[str, dict]:
    path = ROOT / "data" / "new-video-gen-library.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    return {item["publicProjectId"]: item for item in items}


def main() -> None:
    library = load_library()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?", choices=sorted(library))
    parser.add_argument("--list", action="store_true", help="renderable projects 목록 출력")
    parser.add_argument("--frames", help='smoke render 범위, 예: "0-29"')
    parser.add_argument("--output", help="출력 MP4 경로")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    if args.list:
        for project_id, item in library.items():
            print(f"{project_id:45} {item['composition']:14} {item['sceneCount']:3} scenes")
        return
    if not args.project:
        parser.error("project 또는 --list가 필요합니다")

    item = library[args.project]
    props = ROOT / "data" / args.project / "props-imported.json"
    output = Path(args.output).resolve() if args.output else ROOT / "output" / f"{args.project}-imported.mp4"
    result = render(
        props,
        output,
        composition=item["composition"],
        frames=args.frames,
        concurrency=args.concurrency,
        repo_root=ROOT,
    )
    print(result)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Supertonic 여성 음성팩 조회, 청취, 재생성, 검증 CLI."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PY = REPO_ROOT / "pipeline" / ".venv" / "bin" / "python"

try:
    from brushvid.voice_presets import (
        VoicePresetError,
        catalog_sha256,
        load_catalog,
        preview_path,
        resolve_voice,
        validate_preview_assets,
        voice_map,
        write_catalog_html,
    )
except ImportError:  # pragma: no cover
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise


def _print_list(catalog: dict) -> None:
    print(f"voice pack {catalog['voicePackVersion']} · {catalog['engine']['model']} · ko")
    print(f"{'ID':11} {'구성':18} {'배지':10} 추천")
    print("-" * 84)
    for preset in catalog["voices"]:
        mix = "+".join(f"{name}:{weight:.0%}" for name, weight in preset["components"].items())
        print(f"{preset['id']:11} {mix:18} {preset['badge']:10} {' · '.join(preset['useCases'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="brushvid Supertonic 여성 음성팩 관리")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="여성 음성 10종 목록")
    show = sub.add_parser("show", help="단일 음성 구성·특징·경로")
    show.add_argument("voice")
    preview = sub.add_parser("preview", help="단일 MP3와 전체 청취 페이지 링크")
    preview.add_argument("voice")
    demo = sub.add_parser("demo", help="실합성 WAV/MP3/HTML 데모 재생성")
    demo.add_argument("--all", action="store_true", required=True, help="10종 전체 생성")
    sub.add_parser("validate", help="catalog/schema/hash/포맷/음량 hard gate")
    args = parser.parse_args()

    try:
        catalog = load_catalog()
        if args.command == "list":
            _print_list(catalog)
            return 0
        if args.command == "show":
            resolved = resolve_voice(args.voice, catalog)
            preset = voice_map(catalog).get(resolved["voicePresetId"])
            data = {**resolved, "preview": str(preview_path(args.voice, catalog=catalog)) if preset else None}
            if preset:
                data.update({k: preset[k] for k in (
                    "badge", "pitch", "pace", "pitchHz", "summary", "useCases", "recommendedSkills"
                )})
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.command == "preview":
            audio = preview_path(args.voice, catalog=catalog)
            page = write_catalog_html(catalog=catalog)
            print(audio)
            print(audio.as_uri())
            print(f"catalog: {page.as_uri()}")
            return 0
        if args.command == "demo":
            script = REPO_ROOT / "scripts" / "generate-supertonic-female-demo.py"
            subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, check=True)
            page = REPO_ROOT / "output" / "supertonic-female-voices-10x10s" / "index.html"
            print(page.as_uri())
            return 0
        rows = validate_preview_assets(catalog=catalog)
        page = write_catalog_html(catalog=catalog)
        for row in rows:
            print(f"PASS {row['id']} {row['path']}")
        print(f"PASS catalogSha256={catalog_sha256(catalog)}")
        print(f"PASS listening={page.as_uri()}")
        return 0
    except (VoicePresetError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

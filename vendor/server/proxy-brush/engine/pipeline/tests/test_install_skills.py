from __future__ import annotations

import json
import subprocess
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DRAW_ROOT = ENGINE_ROOT.parent
INSTALLER = DRAW_ROOT / "scripts" / "install_skills.py"
CATALOG = json.loads((DRAW_ROOT / "skills" / "catalog.json").read_text(encoding="utf-8"))
SKILL_IDS = {item["id"] for item in CATALOG["skills"]}


def _run(target: Path, *, check: bool = True):
    args = ["python3", str(INSTALLER), "--target-dir", str(target)]
    if check:
        args.append("--check")
    return subprocess.run(args, cwd=DRAW_ROOT, text=True, capture_output=True, check=False)


def test_installer_copies_real_files_and_is_idempotent(tmp_path: Path):
    target = tmp_path / "skills"
    first = _run(target, check=False)
    assert first.returncode == 0, first.stderr
    assert {path.name for path in target.iterdir()} == SKILL_IDS
    assert not any(path.is_symlink() for path in target.rglob("*"))
    verified = _run(target)
    assert verified.returncode == 0, verified.stderr
    second = _run(target, check=False)
    assert second.returncode == 0
    assert "KEEP" in second.stdout


def test_installer_preserves_different_existing_directory(tmp_path: Path):
    target = tmp_path / "skills"
    existing = target / "brush-video"
    existing.mkdir(parents=True)
    marker = existing / "user-owned.txt"
    marker.write_text("keep", encoding="utf-8")
    result = _run(target, check=False)
    assert result.returncode == 1
    assert marker.read_text(encoding="utf-8") == "keep"


def test_installer_check_rejects_missing_copy(tmp_path: Path):
    result = _run(tmp_path / "missing")
    assert result.returncode == 1
    assert "FAIL" in result.stdout

"""Remotion 번들에 현재 props가 참조하는 public 최상위 자산만 제공한다."""
from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


def public_roots_for_props(props_path: str | Path, public_root: str | Path) -> list[Path]:
    props = json.loads(Path(props_path).read_text(encoding="utf-8"))
    root = Path(public_root).resolve()
    names: set[str] = {str(props.get("projectId") or ""), "brush-draw"}

    def visit(value) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and "/" in value and not value.startswith(("http://", "https://")):
            names.add(value.split("/", 1)[0])

    visit(props)
    return sorted((root / name for name in names if name and (root / name).exists()),
                  key=lambda p: p.name)


@contextmanager
def scoped_public_dir(props_path: str | Path, repo_root: str | Path | None = None, *,
                      public_root: str | Path | None = None):
    """전체 public 대신 현재 projectId와 참조 자산만 가진 임시 public root를 만든다."""
    if public_root is None:
        if repo_root is None:
            raise ValueError("repo_root or public_root is required")
        public_root = Path(repo_root).resolve() / "public"
    sources = public_roots_for_props(props_path, Path(public_root).resolve())
    with tempfile.TemporaryDirectory(prefix="brushvid-public-") as tmp:
        scoped = Path(tmp)
        for source in sources:
            dest = scoped / source.name
            try:
                dest.symlink_to(source, target_is_directory=source.is_dir())
            except OSError:
                if source.is_dir():
                    shutil.copytree(source, dest)
                else:
                    shutil.copy2(source, dest)
        yield scoped

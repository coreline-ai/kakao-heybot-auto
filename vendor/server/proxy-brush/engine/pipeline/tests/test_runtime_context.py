from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from brushvid.runtime import RuntimeContext, RuntimePathError, StageEventWriter


def make_context(tmp_path: Path) -> RuntimeContext:
    engine = tmp_path / "engine"
    (engine / "public" / "brush-draw").mkdir(parents=True)
    (engine / "public" / "brush-draw" / "brush.png").write_bytes(b"brush")
    return RuntimeContext.create(engine, tmp_path / "jobs" / "job-1")


def test_runtime_context_separates_engine_and_workspace(tmp_path: Path):
    context = make_context(tmp_path)
    assert context.engine_root != context.workspace_root
    assert (context.public_root / "brush-draw" / "brush.png").read_bytes() == b"brush"
    for path in (context.input_root, context.data_root, context.output_root, context.logs_root):
        assert path.is_dir()
        assert context.workspace_root in path.parents


def test_runtime_context_rejects_nested_or_symlink_workspace(tmp_path: Path):
    engine = tmp_path / "engine"
    engine.mkdir()
    with pytest.raises(RuntimePathError, match="disjoint"):
        RuntimeContext.create(engine, engine / "runtime")
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(RuntimePathError, match="symlink"):
        RuntimeContext.create(engine, link)


def test_project_inputs_must_be_real_files_under_input_root(tmp_path: Path):
    context = make_context(tmp_path)
    project = context.input_root / "project.yaml"
    image = context.input_root / "image.png"
    project.write_text("projectId: fixture\n", encoding="utf-8")
    image.write_bytes(b"png")
    cfg = SimpleNamespace(srt=None, audio=None, script=None, bg_images=[image])
    context.validate_project_inputs(cfg, project)
    cfg.bg_images = [tmp_path / "outside.png"]
    with pytest.raises(RuntimePathError, match="escapes"):
        context.validate_project_inputs(cfg, project)


@pytest.mark.parametrize(
    ("value", "message"),
    [("../outside.png", "escapes"), ("missing.png", "regular file"), ("asset.exe", "unsupported extension")],
)
def test_declared_project_inputs_reject_unsafe_paths(tmp_path: Path, value: str, message: str):
    context = make_context(tmp_path)
    project = context.input_root / "project.yaml"
    project.write_text(
        f"projectId: fixture\nbackground:\n  strategy: user-images\n  images: [{value}]\n",
        encoding="utf-8",
    )
    (context.input_root / "asset.exe").write_bytes(b"not-an-image")
    with pytest.raises(RuntimePathError, match=message):
        context.validate_project_source(project)


def test_declared_project_input_rejects_symlink(tmp_path: Path):
    context = make_context(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"png")
    linked = context.input_root / "linked.png"
    linked.symlink_to(outside)
    project = context.input_root / "project.yaml"
    project.write_text(
        "projectId: fixture\nbackground:\n  strategy: user-images\n  images: [linked.png]\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimePathError, match="symlink"):
        context.validate_project_source(project)


def test_stage_event_writer_is_monotonic_and_sanitized(tmp_path: Path):
    context = make_context(tmp_path)
    event_path = context.logs_root / "events.jsonl"
    writer = StageEventWriter(event_path, context)
    writer.emit("render", "started", {"path": str(context.output_root / "video.mp4"), "secretToken": "hidden"})
    writer.emit("render", "completed", {"ok": True})
    rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert [row["phase"] for row in rows] == ["started", "completed"]
    assert rows[0]["payload"] == {"path": "output/video.mp4"}
    assert str(tmp_path) not in event_path.read_text(encoding="utf-8")


def test_event_writer_ignores_partial_last_line_when_resuming(tmp_path: Path):
    context = make_context(tmp_path)
    event_path = context.logs_root / "events.jsonl"
    event_path.write_text('{"sequence":4}\n{"sequence":', encoding="utf-8")
    writer = StageEventWriter(event_path, context)
    writer.emit("qa", "failed", {"errorType": "RuntimeError"})
    last = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["sequence"] == 5

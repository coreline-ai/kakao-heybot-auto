"""Full Color Motion의 별도 props/config 계약 테스트."""
import json

import pytest

from brushvid.motion import build_motion_props, validate_motion_props, write_motion_props
from brushvid.project import MotionConfig, MotionSceneConfig, ProjectConfig, load_project


def test_motion_props_are_schema_valid_and_scale_brush_for_uhd(tmp_path):
    cfg = ProjectConfig(
        project_id="motion-uhd", fmt="youtube", render_resolution="uhd",
        title="원본 색상 모션", motion=MotionConfig(
            default=MotionSceneConfig(movement="arc-right", effect="mist", intensity=1.4),
            scenes=(MotionSceneConfig(movement="push-in", effect="rays", reveal="brush", reveal_frames=90),),
        ),
    )
    props = build_motion_props(cfg, [
        {"durationInFrames": 300, "cues": [{"text": "첫 장면", "from": 10, "to": 120}]},
    ])
    assert validate_motion_props(props)["scenes"][0]["image"] == "motion-uhd/bg/scene-01.png"
    scene = props["scenes"][0]
    assert scene["reveal"]["routes"] == "motion-uhd/routes/scene-01.motion-reveal.routes.json"
    assert scene["reveal"]["cursor"]["w"] == 1112
    assert props["brush"]["w"] == 1112
    assert scene["topTitle"]["lines"] == ["원본 색상 모션"]
    out = write_motion_props(props, tmp_path / "props.json")
    assert json.loads(out.read_text(encoding="utf-8"))["scenes"][0]["effect"] == "rays"


def test_motion_props_keep_cues_only_when_overlays_are_enabled():
    scene_data = [{"durationInFrames": 300, "cues": [{"text": "QA cue", "from": 1, "to": 20}]}]
    base = dict(project_id="motion-overlay", motion=MotionConfig())
    shown = build_motion_props(ProjectConfig(**base), scene_data)
    hidden = build_motion_props(ProjectConfig(**base, overlays="none"), scene_data)
    assert shown["scenes"][0]["cues"]
    assert hidden["scenes"][0]["cues"] == []
    assert hidden["scenes"][0]["captionsVisible"] is False


def test_project_rejects_full_color_motion_without_required_source_contract(tmp_path):
    image = tmp_path / "source.png"
    image.write_bytes(b"placeholder")
    project = tmp_path / "bad-fit.yaml"
    project.write_text("""
projectId: motion-bad-fit
background:
  strategy: user-images
  images: [source.png]
  fit: contain
drawing:
  profile: full-color-motion
motion: {}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="background.fit: cover"):
        load_project(project)

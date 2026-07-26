from __future__ import annotations

from pathlib import Path

import pytest

from brushvid.piano_auto_bgm import build_request
from brushvid.project import BgmConfig, ProjectConfig


@pytest.mark.parametrize("profile", [
    "brush", "full-color-motion", "pen", "pen-brush", "dark-random-brush",
    "cosmic-random-brush", "storybook-full-bleed",
])
def test_build_request_has_a_stable_audio_preset_for_each_shared_video_profile(profile):
    request = build_request(
        ProjectConfig(project_id=f"profile-{profile}", drawing_profile=profile),
        30.0,
        BgmConfig(mode="piano-auto"),
    )
    assert request["engine"] == "stable-audio-3-mlx"
    assert request["preset"]
    assert "piano" in request["prompt"].lower()


def test_build_request_maps_profile_context_to_stable_audio_prompt():
    cfg = ProjectConfig(
        project_id="dark-sea",
        title="심해의 빛",
        bg_subject="깊은 바다와 푸른 광원",
        drawing_profile="cosmic-random-brush",
        fmt="youtube",
        ambient_cues=["어둠 속에서 빛이 열린다"],
        seed=77,
    )
    request = build_request(cfg, 30.0, BgmConfig(mode="piano-auto", cfg=2.5, steps=8))
    assert request["engine"] == "stable-audio-3-mlx"
    assert request["preset"] == "mystery-horror-piano"
    assert request["durationSec"] == 30.0
    assert "dark mysterious deep-space atmosphere" in request["prompt"]
    assert "심해의 빛" in request["prompt"]
    assert request["seed"] == 77


def test_build_request_accepts_explicit_prompt_and_negative_prompt():
    cfg = ProjectConfig(project_id="custom-piano", drawing_profile="brush")
    request = build_request(cfg, 30.0, BgmConfig(
        mode="piano-auto", prompt="heroic solo piano", negative_prompt="vocals", cfg=1.5, steps=4,
    ))
    assert request["prompt"] == "heroic solo piano"
    assert request["negativePrompt"] == "vocals"
    assert request["cfg"] == 1.5 and request["steps"] == 4

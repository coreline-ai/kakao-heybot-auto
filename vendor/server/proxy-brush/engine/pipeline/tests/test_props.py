"""props.py 테스트 — TC-3.3(jsonschema 검증 + schemaVersion=1)."""
import jsonschema
import pytest

from brushvid.props import (BRUSH_NO_PULSE_PRESET, SCHEMA_PATH, build_props,
                            build_scene, fit_brush_completion_timing,
                            validate_brush_completion_timing, validate_props)


def test_tc_3_3_props_validate():
    """TC-3.3: 빌더 출력이 jsonschema 검증 통과 + schemaVersion=1."""
    scene = build_scene("scene-01", "demo/routes.json", 300,
                        cues=[{"text": "안녕", "from": 30, "to": 90}],
                        top_title={"lines": ["첫 줄"], "enterAt": 10})
    props = build_props("demo-project", [scene], audio="demo/audio.mp3")
    validated = validate_props(props)
    assert validated["schemaVersion"] == 1
    assert validated["projectId"] == "demo-project"
    assert validated["scenes"][0]["routes"] == "demo/routes.json"


def test_tc_3_3_schema_is_consumed_not_defined():
    """스키마 파일은 리포의 schema/render-props.schema.json 을 소비한다."""
    assert SCHEMA_PATH.name == "render-props.schema.json"


def test_invalid_props_rejected():
    """스키마 위반(scenes 비움, 잘못된 format)은 ValidationError."""
    with pytest.raises(jsonschema.ValidationError):
        validate_props({"schemaVersion": 1, "projectId": "x", "scenes": []})
    scene = build_scene("s1", None, 100)
    bad = build_props("x", [scene], fmt="youtub")
    with pytest.raises(jsonschema.ValidationError):
        validate_props(bad)


def test_golden_single_props_pass():
    """기존 golden-single props 도 같은 스키마를 통과한다 (회귀 가드)."""
    import json
    golden = SCHEMA_PATH.parent.parent / "data" / "golden-single" / "props.json"
    validate_props(json.loads(golden.read_text(encoding="utf-8")))


def test_brush_no_pulse_preset_is_explicit_and_schema_valid():
    scene = build_scene("scene-01", "demo/routes.json", 300, **BRUSH_NO_PULSE_PRESET)
    props = build_props("no-pulse", [scene])
    validate_props(props)
    assert scene["completionMode"] == "integrated-develop"
    assert scene["colorSettleFrames"] == 18
    assert scene["prewashOpacity"] == 0
    assert scene["outroWashOpacity"] == 1.0


def test_scene_schema_accepts_a_distinct_prewash_source_image():
    scene = build_scene(
        "scene-01", "demo/routes.json", 300,
        prewashImage="demo/bg/scene-01.png",
        prewashOpacity=0.5,
        prewashFrames=12,
        prewashFadeOutFrames=12,
        prewashBlur=12,
    )
    props = build_props("blurred-poster", [scene])
    validate_props(props)
    assert scene["prewashImage"] == "demo/bg/scene-01.png"


def test_completion_timing_prefers_36_18_when_tail_is_long_enough():
    timing = fit_brush_completion_timing(300, 206, outro_fade_frames=24)
    assert timing["developFrames"] == 36
    assert timing["colorSettleFrames"] == 18
    assert timing["colorSettleEnd"] == 260
    assert timing["outroStart"] == 276


def test_completion_timing_scales_without_overlap_for_short_scene():
    timing = fit_brush_completion_timing(240, 175, outro_fade_frames=24)
    assert timing["developFrames"] == 19
    assert timing["colorSettleFrames"] == 10
    assert timing["colorSettleEnd"] + timing["holdFrames"] <= timing["outroStart"]


def test_completion_timing_rejects_impossible_tail():
    with pytest.raises(ValueError, match="완료 시간 부족"):
        fit_brush_completion_timing(120, 90, outro_fade_frames=24)


def test_serialized_completion_timing_rejects_overlap():
    scene = build_scene("scene-bad", "demo/routes.json", 300, **BRUSH_NO_PULSE_PRESET)
    strokes = [{"end": 230}]
    with pytest.raises(ValueError, match="outro 중첩"):
        validate_brush_completion_timing(scene, strokes)

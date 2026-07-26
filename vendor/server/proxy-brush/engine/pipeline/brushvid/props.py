"""props.py — render-props 빌더 + jsonschema 검증.

스키마의 유일한 진실은 TS(Zod)에서 내보낸 schema/render-props.schema.json —
여기서는 소비(검증)만 하고 형태를 재정의하지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

# 리포 루트/스키마 위치 (pipeline/brushvid/props.py 기준 2단계 위가 리포 루트)
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "render-props.schema.json"

_schema_cache: dict[str, dict] = {}


# 일반 가로 brush의 안전한 완료 연출. pen/pen-brush/shorts/cosmic은 각 프로파일
# 프리셋을 그대로 사용하며 이 값을 자동 상속하지 않는다.
BRUSH_NO_PULSE_PRESET = {
    "faint": 0.88,
    "edgeFeather": 14,
    "linearDraw": True,
    "completionMode": "integrated-develop",
    "developFrames": 36,
    "colorSettleFrames": 18,
    "previewOpacity": 0,
    "prewashOpacity": 0,
    "prewashFrames": 0,
    "prewashHoldFrames": 0,
    "prewashFadeOutFrames": 0,
    "prewashBlur": 0,
    "outroFadeFrames": 24,
    "outroWashOpacity": 1.0,
    "outroBlur": 0,
}

BRUSH_MIN_HOLD_FRAMES = 12
BRUSH_MIN_DEVELOP_FRAMES = 12
BRUSH_MIN_COLOR_SETTLE_FRAMES = 6


def fit_brush_completion_timing(duration_in_frames: int, last_stroke_end: float, *,
                                outro_fade_frames: int = 24,
                                hold_frames: int = BRUSH_MIN_HOLD_FRAMES,
                                preferred_develop_frames: int = 36,
                                preferred_color_settle_frames: int = 18) -> dict:
    """기존 route 타이밍을 바꾸지 않고 완료/색감 시간을 씬 꼬리에 맞춘다.

    선호값 36/18을 우선 사용하되 짧은 narration 씬은 2:1 비율로 결정적으로
    축소한다. 최소 12/6조차 확보되지 않으면 연출을 겹치지 않고 명시적으로 실패한다.
    """
    duration = int(duration_in_frames)
    outro = max(0, int(outro_fade_frames))
    hold = max(0, int(hold_frames))
    outro_start = duration - outro
    available = int(outro_start - hold - float(last_stroke_end))
    minimum = BRUSH_MIN_DEVELOP_FRAMES + BRUSH_MIN_COLOR_SETTLE_FRAMES
    if available < minimum:
        raise ValueError(
            "brush 완료 시간 부족: "
            f"duration={duration}, lastStrokeEnd={last_stroke_end:.2f}, "
            f"outroStart={outro_start}, hold={hold}, available={available}, minimum={minimum}"
        )

    preferred_total = preferred_develop_frames + preferred_color_settle_frames
    total = min(available, preferred_total)
    develop = max(BRUSH_MIN_DEVELOP_FRAMES, round(total * 2 / 3))
    settle = total - develop
    if settle < BRUSH_MIN_COLOR_SETTLE_FRAMES:
        settle = BRUSH_MIN_COLOR_SETTLE_FRAMES
        develop = total - settle

    color_settle_end = float(last_stroke_end) + develop + settle
    return {
        "lastStrokeEnd": round(float(last_stroke_end), 2),
        "developFrames": int(develop),
        "colorSettleFrames": int(settle),
        "colorSettleEnd": round(color_settle_end, 2),
        "holdFrames": hold,
        "outroStart": outro_start,
        "availableFrames": available,
    }


def validate_brush_completion_timing(scene: dict, strokes: list[dict], *,
                                     hold_frames: int = BRUSH_MIN_HOLD_FRAMES) -> dict:
    """직렬화된 integrated-develop 씬이 완료→홀드→outro 순서를 지키는지 검사."""
    last_end = max((float(s.get("end", 0)) for s in strokes), default=0.0)
    duration = int(scene["durationInFrames"])
    outro = int(scene.get("outroFadeFrames", 0))
    develop = int(scene.get("developFrames", 0))
    settle = int(scene.get("colorSettleFrames", 0))
    outro_start = duration - outro
    settle_end = last_end + develop + settle
    if settle_end + hold_frames > outro_start + 1e-6:
        raise ValueError(
            f"{scene.get('id', '<scene>')}: 완료 연출과 outro 중첩 — "
            f"lastStrokeEnd={last_end:.2f}, develop={develop}, settle={settle}, "
            f"hold={hold_frames}, outroStart={outro_start}"
        )
    return {
        "lastStrokeEnd": round(last_end, 2),
        "developEnd": round(last_end + develop, 2),
        "colorSettleEnd": round(settle_end, 2),
        "holdFrames": hold_frames,
        "outroStart": outro_start,
    }


def load_schema(schema_path: str | Path = SCHEMA_PATH) -> dict:
    """render-props JSON Schema 로드 (캐시)."""
    key = str(schema_path)
    if key not in _schema_cache:
        _schema_cache[key] = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    return _schema_cache[key]


def default_brush() -> dict:
    """공용 붓 커서 에셋 블록 (public/brush-draw/brush.png)."""
    return {"src": "brush-draw/brush.png", "w": 556, "h": 344, "tipx": 25, "tipy": 315}


def build_scene(scene_id: str, routes: str | None, duration_in_frames: int, *,
                cues: list[dict] | None = None, top_title: dict | None = None,
                subtitle_style: dict | None = None, natural_effects: dict | None = None,
                brush_dynamics: dict | None = None, **overrides) -> dict:
    """씬 1개 빌드. 기본 연출 값은 golden-single 프로파일을 따른다."""
    scene: dict = {
        "id": scene_id,
        "durationInFrames": int(duration_in_frames),
        "faint": 0.68,
        "edgeFeather": 12,
        "linearDraw": True,
        "developFrames": 20,
        "prewashOpacity": 0.65,
        "prewashFrames": 36,
        "prewashHoldFrames": 10,
        "prewashBlur": 12,
    }
    if routes is not None:
        scene["routes"] = routes
    scene["brushDynamics"] = brush_dynamics if brush_dynamics is not None else {
        "drawSpeedScale": 1.0,
        "touchScale": 1.45,
        "touchJitter": 0.22,
        "randomizeOrder": True,
        "randomReverse": True,
        "seed": 7701,
    }
    if cues:
        scene["cues"] = cues
    if top_title:
        scene["topTitle"] = top_title
    if subtitle_style:
        scene["subtitleStyle"] = subtitle_style
    if natural_effects:
        scene["naturalEffects"] = natural_effects
    scene.update(overrides)
    return scene


def build_props(project_id: str, scenes: list[dict], *, title: str | None = None,
                fmt: str = "youtube", audio: str | None = None, paper: str = "#fbfaf6",
                brush: dict | None = None) -> dict:
    """canonical render-props 빌드 (schemaVersion=1)."""
    props: dict = {
        "schemaVersion": 1,
        "projectId": project_id,
        "format": fmt,
        "paper": paper,
        "brush": brush if brush is not None else default_brush(),
        "scenes": scenes,
    }
    if title is not None:
        props["title"] = title
    if audio is not None:
        props["audio"] = audio
    return props


def validate_props(props: dict, schema_path: str | Path = SCHEMA_PATH) -> dict:
    """schema/render-props.schema.json 으로 검증. 위반 시 jsonschema.ValidationError."""
    jsonschema.validate(instance=props, schema=load_schema(schema_path))
    return props


def write_props(props: dict, out_path: str | Path, schema_path: str | Path = SCHEMA_PATH) -> Path:
    """검증 후 저장."""
    validate_props(props, schema_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

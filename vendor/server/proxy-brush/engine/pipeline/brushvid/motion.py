"""Full Color Motion props 계약.

원본 색상을 보존한 정지 이미지 → 2D movement/effect/crossfade renderer의 props를
조립한다. brush props와 schema를 공유하지 않아 양쪽 기본값이 결합되지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from .project import MotionSceneConfig, ProjectConfig
from .props import default_brush

REPO_ROOT = Path(__file__).resolve().parents[2]
MOTION_SCHEMA_PATH = REPO_ROOT / "schema" / "full-color-motion-props.schema.json"

_schema_cache: dict[str, dict] = {}


def load_motion_schema(schema_path: str | Path = MOTION_SCHEMA_PATH) -> dict:
    key = str(schema_path)
    if key not in _schema_cache:
        _schema_cache[key] = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    return _schema_cache[key]


def validate_motion_props(props: dict, schema_path: str | Path = MOTION_SCHEMA_PATH) -> dict:
    jsonschema.validate(instance=props, schema=load_motion_schema(schema_path))
    return props


def motion_route_rel(project_id: str, index: int) -> str:
    return f"{project_id}/routes/scene-{index + 1:02d}.motion-reveal.routes.json"


def requires_brush_routes(cfg: ProjectConfig) -> bool:
    return bool(cfg.motion and any(
        cfg.motion.scene_for(index).reveal == "brush"
        for index in range(len(cfg.motion.scenes) or cfg.ambient_scenes)
    ))


def _top_title(cfg: ProjectConfig, index: int) -> dict | None:
    if index != 0 or not cfg.title or cfg.overlays == "none":
        return None
    return {
        "kicker": "FULL COLOR MOTION",
        "lines": [cfg.title],
        "x": 72, "y": 58, "width": 980,
        "enterAt": 7, "accent": "#d7bc7c", "color": "#f7f5ee",
        "fontSize": 42, "kickerFontSize": 14,
    }


def _motion_brush(cfg: ProjectConfig) -> dict:
    """Route 좌표계와 같은 visual scale의 공용 brush cursor를 만든다."""
    scale = (3840 if cfg.fmt == "youtube" and cfg.render_resolution == "uhd"
             else (1080 if cfg.fmt == "shorts" else 1920)) / 1920
    brush = dict(default_brush())
    for key in ("w", "h", "tipx", "tipy"):
        brush[key] = round(float(brush[key]) * scale, 3)
    return brush


def build_motion_props(
    cfg: ProjectConfig,
    scenes: list[dict[str, Any]],
    *,
    shorts_subtitle_style: dict | None = None,
) -> dict:
    """Pipeline scenes.json + MotionConfig → schema-validated motion props."""
    if cfg.motion is None:
        raise ValueError("full-color-motion props에는 MotionConfig가 필요함")
    if cfg.motion.scenes and len(cfg.motion.scenes) != len(scenes):
        raise ValueError(
            "motion.scenes 수가 실제 cue scene 수와 다름: "
            f"{len(cfg.motion.scenes)} != {len(scenes)}"
        )
    output_scenes: list[dict] = []
    motion_brush = _motion_brush(cfg)
    for index, source_scene in enumerate(scenes):
        spec: MotionSceneConfig = cfg.motion.scene_for(index)
        reveal: dict = {"mode": spec.reveal, "frames": spec.reveal_frames}
        if spec.reveal == "brush":
            reveal["routes"] = motion_route_rel(cfg.project_id, index)
            reveal["cursor"] = motion_brush
        item = {
            "id": f"scene-{index + 1:02d}",
            "image": f"{cfg.project_id}/bg/scene-{index + 1:02d}.png",
            "durationInFrames": int(source_scene["durationInFrames"]),
            "movement": spec.movement,
            "effect": spec.effect,
            "intensity": spec.intensity,
            "reveal": reveal,
            "cues": [] if cfg.overlays == "none" else (source_scene.get("cues") or []),
            "captionsVisible": cfg.overlays != "none",
        }
        title = _top_title(cfg, index)
        if title:
            item["topTitle"] = title
        if cfg.subtitle_style:
            item["subtitleStyle"] = cfg.subtitle_style
        elif cfg.fmt == "shorts" and shorts_subtitle_style:
            item["subtitleStyle"] = shorts_subtitle_style
        output_scenes.append(item)
    return {
        "schemaVersion": 1,
        "projectId": cfg.project_id,
        "title": cfg.title,
        "format": cfg.fmt,
        "audio": None,
        "brush": motion_brush,
        "scenes": output_scenes,
    }


def write_motion_props(props: dict, out_path: str | Path,
                       schema_path: str | Path = MOTION_SCHEMA_PATH) -> Path:
    validate_motion_props(props, schema_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(props, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out

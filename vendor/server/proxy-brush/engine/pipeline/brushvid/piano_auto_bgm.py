"""Bridge the Stable Audio piano candidate into the shared video build mix.

The standalone ``piano-bgm`` skill owns the Stable Audio adapter and listening
approval semantics. This module only translates a ``project.yaml`` context into
that request and prepares the candidate files consumed by ``bin/build.py``.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .piano_bgm import (
    OUTPUT_ROOT,
    PROJECTS_ROOT,
    media_tool,
    normalize_request,
    qa_generated_project,
    sha256_json,
    write_request,
)
from .mix import normalize_track
from .project import BgmConfig, ProjectConfig
from .stable_audio import StableAudioError, generate as stable_audio_generate


class PianoAutoBgmError(RuntimeError):
    """Raised when an explicit piano-auto request cannot produce a candidate."""


PRESET_BY_PROFILE = {
    "brush": "ambient-piano",
    "pen": "minimal-ambient",
    "pen-brush": "cinematic-piano",
    "cosmic-random-brush": "mystery-horror-piano",
    "dark-random-brush": "mystery-horror-piano",
    "full-color-motion": "cinematic-piano",
    "progressive-frame-sequence": "cinematic-piano",
    "storybook-full-bleed": "fantasy-piano",
}


def _safe_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value[:limit]


def _request_id(project_id: str) -> str:
    candidate = re.sub(r"[^a-z0-9-]+", "-", project_id.lower()).strip("-")
    if len(candidate) >= 2:
        return candidate[:63]
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:10]
    return f"piano-auto-{digest}"


def _prompt_context(cfg: ProjectConfig) -> str:
    pieces = [cfg.title, cfg.bg_subject, *cfg.ambient_cues[:2]]
    return ", ".join(item for item in (_safe_text(piece, 90) for piece in pieces) if item)


def build_request(cfg: ProjectConfig, duration_sec: float, bgm_cfg: BgmConfig) -> dict[str, Any]:
    profile = cfg.drawing_profile
    preset = PRESET_BY_PROFILE.get(profile, "new-age")
    orientation = "vertical short-form" if cfg.fmt == "shorts" else "wide video"
    context = _prompt_context(cfg) or "calm visual storytelling"
    style = {
        "brush": "gentle flowing watercolor atmosphere",
        "pen": "clear thoughtful sketching atmosphere",
        "pen-brush": "warm illustrative reveal atmosphere",
        "cosmic-random-brush": "dark mysterious deep-space atmosphere",
        "full-color-motion": "elegant cinematic color-motion atmosphere",
        "storybook-full-bleed": "warm magical storybook atmosphere",
    }.get(profile, "cinematic visual atmosphere")
    prompt = bgm_cfg.prompt or (
        f"{style}, solo grand piano, expressive instrumental background music for {orientation}, "
        f"coherent melodic development, restrained accompaniment, no vocals, visual mood: {context}"
    )
    raw = {
        "projectId": _request_id(cfg.project_id),
        "kind": "piano-bgm",
        "durationSec": duration_sec,
        "preset": preset,
        "mood": _safe_text(f"{style} {context}", 80) or "balanced",
        "purpose": "background",
        "engine": "stable-audio-3-mlx",
        "prompt": _safe_text(prompt, 1000),
        "cfg": bgm_cfg.cfg,
        "steps": bgm_cfg.steps,
        "key": "C-minor" if profile in {"cosmic-random-brush", "dark-random-brush"} else None,
        "tempoBpm": 56 if profile in {"cosmic-random-brush", "dark-random-brush"} else 68,
        "seed": max(0, min(2147483647, int(cfg.seed))),
        "output": {"distribution": cfg.fmt, "preview": True},
    }
    if bgm_cfg.negative_prompt:
        raw["negativePrompt"] = _safe_text(bgm_cfg.negative_prompt, 1000)
    if raw["key"] is None:
        del raw["key"]
    return normalize_request(raw)


def _json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def _paths(project_id: str, *, output_root: Path) -> dict[str, Path]:
    root = output_root / project_id
    return {
        "root": root,
        "source": root / "source.wav",
        "generation": root / "generation.json",
        "raw": root / "raw-48k24.wav",
        "master": root / "master-48k24.wav",
        "delivery": root / f"{project_id}-44k16.wav",
        "manifest": root / "generated-bgm-manifest.json",
        "qa": root / "qa.json",
    }


def _postprocess(source: Path, paths: dict[str, Path], duration_sec: float) -> None:
    ffmpeg = media_tool("ffmpeg")
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(paths["raw"])], check=True)
    # Reuse the build pipeline's two-pass loudnorm measurement. A single-pass
    # loudnorm can leave short/high-dynamic-range piano candidates several LU
    # below target, which would incorrectly fail the generated-candidate QA.
    normalized = paths["root"] / ".master-normalized-s16.wav"
    normalize_track(paths["raw"], normalized, target_lufs=-23.0, duration_sec=duration_sec)
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(normalized),
                    "-ar", "48000", "-ac", "2", "-c:a", "pcm_s24le", str(paths["master"])], check=True)
    normalized.unlink(missing_ok=True)
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(paths["master"]),
                    "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(paths["delivery"])], check=True)


def _ready(paths: dict[str, Path], request: dict[str, Any]) -> bool:
    if not all(paths[key].is_file() for key in ("source", "generation", "raw", "master", "delivery", "manifest", "qa")):
        return False
    generation = _json(paths["generation"])
    return bool(generation and generation.get("pianoRequestSha256") == sha256_json(request))


def build_candidate(
    cfg: ProjectConfig,
    duration_sec: float,
    bgm_cfg: BgmConfig,
    *,
    output_root: Path = OUTPUT_ROOT,
    projects_root: Path = PROJECTS_ROOT,
    stable_audio_root: str | Path | None = None,
) -> dict[str, Any]:
    """Generate/reuse a project-scoped candidate and return its delivery path."""
    if not 15.0 <= duration_sec <= 120.0:
        raise PianoAutoBgmError("piano-auto Stable Audio 후보는 15~120초 영상에서만 생성할 수 있습니다")
    request = build_request(cfg, duration_sec, bgm_cfg)
    paths = _paths(cfg.project_id, output_root=output_root)
    paths["root"].mkdir(parents=True, exist_ok=True)
    write_request(projects_root / cfg.project_id / "request.yaml", request)
    if not _ready(paths, request):
        old_generation = _json(paths["generation"])
        old_manifest = _json(paths["manifest"])
        preserve_approval = bool(
            old_generation
            and old_generation.get("pianoRequestSha256") == sha256_json(request)
            and old_manifest
            and old_manifest.get("status") == "APPROVED"
        )
        try:
            generation = stable_audio_generate(request, paths["source"], root=stable_audio_root, force=True)
        except StableAudioError as exc:
            raise PianoAutoBgmError(str(exc)) from exc
        generation["pianoRequestSha256"] = sha256_json(request)
        paths["generation"].write_text(json.dumps(generation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            _postprocess(paths["source"], paths, duration_sec)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PianoAutoBgmError(f"piano-auto 후보 후처리 실패: {exc}") from exc
        qa = qa_generated_project(cfg.project_id, projects_root=projects_root, output_root=output_root)
        if qa.get("status") == "TECHNICAL_FAIL":
            raise PianoAutoBgmError("piano-auto 후보 기술 QA 실패")
        if preserve_approval and old_manifest:
            _restore_approval(paths, old_manifest)
    manifest = _json(paths["manifest"]) or {}
    return {
        "mode": "piano-auto",
        "engine": "stable-audio-3-mlx",
        "source": str(paths["delivery"]),
        "manifest": str(paths["manifest"]),
        "status": manifest.get("status", "PENDING_USER_LISTENING"),
        "request": request,
    }


def _restore_approval(paths: dict[str, Path], old_manifest: dict[str, Any]) -> None:
    """Preserve approval only when the caller reused the exact candidate request."""
    manifest = _json(paths["manifest"]) or {}
    qa = _json(paths["qa"]) or {}
    human = old_manifest.get("humanListening")
    if not human:
        return
    manifest["status"] = "APPROVED"
    manifest["humanListening"] = human
    qa["status"] = "APPROVED"
    qa["humanListening"] = human
    paths["manifest"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["qa"].write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = ["PianoAutoBgmError", "PRESET_BY_PROFILE", "build_candidate", "build_request"]

"""Local-only runtime capability probes. No imports that can download models."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .runtime import RuntimeContext


def _state(available: bool, unavailable_code: str = "DEPENDENCY_MISSING") -> dict[str, Any]:
    return {"available": bool(available), "code": "OK" if available else unavailable_code}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _nonempty_dir(path: Path) -> bool:
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False


def _browser_available(context: RuntimeContext) -> bool:
    manifest = context.engine_root.parent / "runtime" / "remotion-browser" / "browser.json"
    try:
        executable = Path(json.loads(manifest.read_text(encoding="utf-8"))["executable"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return executable.is_file()


def _stable_audio_available() -> bool:
    configured = os.environ.get("STABLE_AUDIO_3_MLX_ROOT")
    if not configured:
        return False
    root = Path(configured).expanduser().resolve()
    required = (
        "sa3",
        "models/mlx/dit_sm-music_f16.npz",
        "models/mlx/same_s_decoder_f32.npz",
        "models/mlx/same_s_encoder_f32.npz",
        "models/mlx/t5gemma_f16.npz",
    )
    return all((root / item).is_file() for item in required) and os.access(root / "sa3", os.X_OK)


def probe_capabilities(context: RuntimeContext) -> dict[str, dict[str, Any]]:
    """Return deterministic sanitized capability states without network or model loading."""
    node = shutil.which("node") is not None
    python = sys.version_info[:2] == (3, 11)
    ffmpeg = shutil.which("ffmpeg") is not None
    ffprobe = shutil.which("ffprobe") is not None
    remotion = (
        node
        and (context.engine_root / "node_modules" / "@remotion" / "cli").is_dir()
        and _browser_available(context)
    )
    whisper_root = context.model_root / "faster-whisper"
    stt_model = _nonempty_dir(whisper_root)
    codex_configured = os.environ.get("DRAW_PROXY_CODEX_BIN")
    codex = bool(
        (codex_configured and Path(codex_configured).expanduser().is_file())
        or shutil.which("codex")
    )
    tts = _module_available("supertonic")
    stable_audio = _stable_audio_available()
    return {
        "node": _state(node),
        "python": _state(python, "UNSUPPORTED_RUNTIME"),
        "ffmpeg": _state(ffmpeg),
        "ffprobe": _state(ffprobe),
        "remotion": _state(remotion, "RUNTIME_MISSING"),
        "preset": _state(True),
        "user-images": _state(True),
        "codex-imagegen": _state(codex, "CAPABILITY_MISSING"),
        "tts": _state(tts, "CAPABILITY_MISSING"),
        "stt": _state(_module_available("faster_whisper") and stt_model, "MODEL_MISSING"),
        "stable-audio": _state(stable_audio, "MODEL_MISSING"),
        "stable-audio-or-samples": _state(stable_audio, "MODEL_MISSING"),
        "i2v": _state(False, "CAPABILITY_MISSING"),
    }

"""Stable Audio 3 MLX adapter used by the piano BGM CLI.

The adapter intentionally talks to the checked-out ``sa3`` wrapper instead of
importing the MLX implementation. This keeps the project dependency-free and
lets the local model installation remain outside the repository.
"""
from __future__ import annotations

import hashlib
import math
import os
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_ID = "stabilityai/stable-audio-3-optimized"
BUNDLE = "sm-music"
DECODER = "same-s"
MAX_SECONDS = 120.0
DEFAULT_ROOT_ENV = "STABLE_AUDIO_3_MLX_ROOT"

_REQUIRED_WEIGHTS = (
    "models/mlx/dit_sm-music_f16.npz",
    "models/mlx/same_s_decoder_f32.npz",
    "models/mlx/same_s_encoder_f32.npz",
    "models/mlx/t5gemma_f16.npz",
)


class StableAudioError(ValueError):
    """Raised when the local Stable Audio runtime cannot generate a candidate."""


def _short_text(value: str | bytes | None, limit: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    value = value.strip()
    return value[-limit:] if len(value) > limit else value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_root(root: str | Path | None = None) -> Path:
    """Resolve an explicit root or the environment-provided local installation."""
    value = root if root is not None else os.environ.get(DEFAULT_ROOT_ENV)
    if not value:
        raise StableAudioError(
            f"Stable Audio MLX 경로가 없습니다. {DEFAULT_ROOT_ENV} 환경변수를 설정하세요."
        )
    return Path(value).expanduser().resolve()


def _runtime_revision(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    return revision or None


def preflight(root: str | Path | None = None) -> dict[str, Any]:
    """Check the local wrapper and all files required for ``sm-music/same-s``."""
    resolved = resolve_root(root)
    if not resolved.is_dir():
        raise StableAudioError(f"Stable Audio MLX 루트 디렉터리가 없습니다: {resolved}")
    executable = resolved / "sa3"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise StableAudioError(f"실행 가능한 Stable Audio wrapper가 없습니다: {executable}")
    missing = [str(resolved / relative) for relative in _REQUIRED_WEIGHTS if not (resolved / relative).is_file()]
    if missing:
        raise StableAudioError("Stable Audio 가중치가 없습니다: " + ", ".join(missing))
    return {
        "root": str(resolved),
        "executable": str(executable),
        "model": MODEL_ID,
        "bundle": BUNDLE,
        "decoder": DECODER,
        "runtime": "mlx-cli",
        "revision": _runtime_revision(resolved),
        "requiredWeights": [str(resolved / relative) for relative in _REQUIRED_WEIGHTS],
    }


def default_prompt(request: dict[str, Any]) -> str:
    """Build a useful instrumental prompt when a request does not provide one."""
    mood = request.get("mood", "balanced")
    preset = str(request.get("preset", "piano-bgm")).replace("-", " ")
    tempo = request.get("tempoBpm", 60)
    purpose = "featured performance" if request.get("purpose") == "featured" else "background music"
    return (
        f"{mood} {preset} solo grand piano, cinematic {purpose}, instrumental only, "
        f"clear melodic development, expressive dynamics, no vocals, {tempo} BPM"
    )


def default_negative_prompt(request: dict[str, Any]) -> str:
    return str(
        request.get(
            "negativePrompt",
            "vocals, singing, lyrics, spoken words, speech, distorted piano, harsh clipping, noise",
        )
    )


def _validate_request(request: dict[str, Any]) -> tuple[float, int, float, int]:
    try:
        seconds = float(request["durationSec"])
        cfg = float(request.get("cfg", 1.0))
        steps = int(request.get("steps", 8))
        seed = int(request["seed"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StableAudioError("Stable Audio 요청에 durationSec/cfg/steps/seed가 필요합니다") from exc
    if not math.isfinite(seconds) or not 15.0 <= seconds <= MAX_SECONDS:
        raise StableAudioError("Stable Audio sm-music durationSec는 15~120초여야 합니다")
    if not math.isfinite(cfg) or not 0.0 <= cfg <= 10.0:
        raise StableAudioError("Stable Audio cfg는 0~10이어야 합니다")
    if not 1 <= steps <= 16:
        raise StableAudioError("Stable Audio steps는 1~16이어야 합니다")
    return seconds, steps, cfg, seed


def _validate_wav(path: Path, expected_seconds: float) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            sample_rate = stream.getframerate()
            sample_width = stream.getsampwidth()
            frame_count = stream.getnframes()
            compression = stream.getcomptype()
    except (EOFError, OSError, wave.Error) as exc:
        raise StableAudioError(f"Stable Audio 출력이 유효한 WAV가 아닙니다: {path}") from exc
    duration = frame_count / sample_rate if sample_rate else 0.0
    if compression != "NONE" or channels != 2 or sample_rate != 44100 or sample_width != 2:
        raise StableAudioError(
            "Stable Audio 출력 형식이 44.1kHz/16-bit/stereo PCM이 아닙니다: "
            f"codec={compression}, rate={sample_rate}, width={sample_width}, channels={channels}"
        )
    if abs(duration - expected_seconds) > 0.02:
        raise StableAudioError(
            f"Stable Audio 출력 길이가 요청과 다릅니다: expected={expected_seconds}, actual={duration:.6f}"
        )
    if frame_count <= 0:
        raise StableAudioError("Stable Audio 출력이 비어 있습니다")
    return {
        "codec": "pcm_s16le",
        "sampleRateHz": sample_rate,
        "bitDepth": sample_width * 8,
        "channels": channels,
        "frames": frame_count,
        "durationSec": duration,
    }


def generate(
    request: dict[str, Any],
    output: str | Path,
    *,
    root: str | Path | None = None,
    force: bool = False,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    """Generate one atomic Stable Audio candidate and return reproducibility metadata."""
    seconds, steps, cfg, seed = _validate_request(request)
    runtime = preflight(root)
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        raise StableAudioError(f"출력 파일이 이미 있습니다. 덮어쓰려면 --force를 사용하세요: {destination}")
    prompt = str(request.get("prompt") or default_prompt(request))
    negative_prompt = default_negative_prompt(request)
    fd, temporary_name = tempfile.mkstemp(prefix=".stable-audio-", suffix=".wav", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    command = [
        runtime["executable"],
        "--prompt", prompt,
        "--negative-prompt", negative_prompt,
        "--cfg", str(cfg),
        "--dit", BUNDLE,
        "--decoder", DECODER,
        "--seconds", str(seconds),
        "--steps", str(steps),
        "--seed", str(seed),
        "--out", str(temporary),
    ]
    try:
        timeout = float(timeout_sec if timeout_sec is not None else max(180.0, seconds * 20.0))
    except (TypeError, ValueError) as exc:
        raise StableAudioError("Stable Audio timeout-sec는 양의 숫자여야 합니다") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise StableAudioError("Stable Audio timeout-sec는 양의 숫자여야 합니다")
    try:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise StableAudioError(f"Stable Audio 생성 timeout ({timeout:.1f}s): {_short_text(exc.stderr)}") from exc
        except OSError as exc:
            raise StableAudioError(f"Stable Audio wrapper 실행 실패: {runtime['executable']}") from exc
        if result.returncode != 0:
            detail = _short_text(result.stderr) or _short_text(result.stdout)
            raise StableAudioError(f"Stable Audio 생성 실패 (exit={result.returncode}): {detail}")
        if not temporary.is_file():
            raise StableAudioError("Stable Audio wrapper가 출력 WAV를 만들지 않았습니다")
        audio = _validate_wav(temporary, seconds)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schemaVersion": 1,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "requestProjectId": request.get("projectId"),
        "model": runtime,
        "parameters": {
            "prompt": prompt,
            "negativePrompt": negative_prompt,
            "cfg": cfg,
            "steps": steps,
            "seconds": seconds,
            "seed": seed,
        },
        "command": command,
        "output": str(destination),
        "sha256": _sha256(destination),
        "audio": audio,
    }


__all__ = [
    "BUNDLE", "DECODER", "MAX_SECONDS", "MODEL_ID", "StableAudioError",
    "default_negative_prompt", "default_prompt", "generate", "preflight", "resolve_root",
]

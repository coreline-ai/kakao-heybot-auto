"""TTS 입력·모델 pin·cache 계약의 공통 상수와 순수 검증 함수."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


ENGINE_IDS = ("supertonic", "melo-ko", "qwen3-base")
NEW_ENGINE_IDS = ("melo-ko", "qwen3-base")
TTS_FIELDS = frozenset({"engine", "voice", "language", "speed", "pauseMs", "timing", "reference"})
ENGINE_FIELDS = {
    "supertonic": frozenset({"engine", "voice", "language", "speed", "pauseMs", "timing"}),
    "melo-ko": frozenset({"engine", "voice", "language", "speed", "pauseMs", "timing"}),
    "qwen3-base": TTS_FIELDS,
}
ENGINE_MODEL_IDS = {
    "supertonic": "supertonic-3",
    "melo-ko": "myshell-ai/MeloTTS-Korean",
    "qwen3-base": "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
}
MODEL_REVISIONS = {
    "melo-ko": "0207e5adfc90129a51b6b03d89be6d84360ed323",
    "qwen3-base": "fd4b254389122332181a7c3db7f27e918eec64e3",
}
ENGINE_PACKAGES = {
    "supertonic": "supertonic==1.3.1",
    "melo-ko": "melotts==0.1.2",
    "qwen3-base": "qwen-tts==0.1.1",
}
ENGINE_LICENSES = {
    "supertonic": {"model": "OpenRAIL-M", "url": "https://huggingface.co/Supertone/supertonic"},
    "melo-ko": {"model": "MIT", "url": "https://huggingface.co/myshell-ai/MeloTTS-Korean"},
    "qwen3-base": {"model": "Apache-2.0", "url": "https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base"},
}
TTS_CACHE_SCHEMA_VERSION = 1
TTS_MANIFEST_SCHEMA_VERSION = 2
ADAPTER_VERSION = "1"
NORMALIZER_VERSION = "1"


def resolve_local_snapshot(
    model_id: str,
    revision: str,
    *,
    explicit_dir: str | Path | None = None,
    required_files: tuple[str, ...] = (),
) -> Path:
    """Hugging Face cache의 pinned snapshot만 resolve한다. 다운로드하지 않는다."""
    if explicit_dir is not None:
        snapshot = Path(explicit_dir).expanduser().resolve()
    else:
        hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
        snapshot = (
            hf_home / "hub" / f"models--{model_id.replace('/', '--')}" / "snapshots" / revision
        ).resolve()
    if not snapshot.is_dir():
        raise FileNotFoundError(f"MODEL_MISSING: pinned snapshot 없음: {snapshot}")
    missing = [name for name in required_files if not (snapshot / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"MODEL_MISSING: {model_id}@{revision} 파일 없음: {', '.join(missing)}"
        )
    return snapshot


def validate_speed(value: Any, *, minimum: float = 0.70, maximum: float = 2.00) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("input.tts.speed 는 숫자여야 함")
    speed = float(value)
    if not math.isfinite(speed):
        raise ValueError("input.tts.speed 는 유한한 숫자여야 함")
    if not minimum <= speed <= maximum:
        raise ValueError(f"input.tts.speed 는 {minimum:.2f}~{maximum:.2f} 범위여야 함 (입력: {speed!r})")
    return speed


def validate_pause_ms(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("input.tts.pauseMs 는 0 이상 정수여야 함")
    if value < 0:
        raise ValueError("input.tts.pauseMs 는 0 이상")
    return value


def normalize_language(engine: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("input.tts.language 는 비어 있지 않은 문자열이어야 함")
    language = value.strip().lower()
    if engine in NEW_ENGINE_IDS and language != "ko":
        raise ValueError(f"{engine}은 language=ko만 지원함 (입력: {value!r})")
    return language


def validate_reference(reference: Any, *, base_dir: Path) -> dict[str, Any]:
    if not isinstance(reference, dict):
        raise ValueError("input.tts.reference 는 매핑이어야 함")
    unknown = sorted(set(reference) - {"audio", "transcript"})
    if unknown:
        raise ValueError(f"input.tts.reference 지원하지 않는 옵션: {', '.join(unknown)}")
    audio_raw = reference.get("audio")
    transcript_raw = reference.get("transcript")
    for name, value in (("audio", audio_raw), ("transcript", transcript_raw)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"input.tts.reference.{name} 는 비어 있지 않은 경로 문자열이어야 함")
    paths: dict[str, Path] = {}
    for name, raw in (("audio", audio_raw), ("transcript", transcript_raw)):
        raw_path_value = Path(raw)
        if raw_path_value.is_absolute() or ".." in raw_path_value.parts:
            raise ValueError(f"input.tts.reference.{name} 프로젝트 밖 상대 경로 탈출 금지")
        raw_path = base_dir / raw
        if raw_path.is_symlink():
            raise ValueError(f"input.tts.reference.{name} symlink 금지")
        path = raw_path.resolve()
        try:
            path.relative_to(base_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"input.tts.reference.{name} 경로가 프로젝트 밖을 가리킴") from exc
        if not path.is_file():
            raise ValueError(f"input.tts.reference.{name} regular file 없음: {path}")
        if name == "transcript":
            try:
                if not path.read_text(encoding="utf-8").strip():
                    raise ValueError("input.tts.reference.transcript 파일이 비어 있음")
            except UnicodeDecodeError as exc:
                raise ValueError("input.tts.reference.transcript는 UTF-8이어야 함") from exc
        paths[name] = path
    return paths


def cache_signature_material(text: str, config: dict[str, Any]) -> dict[str, Any]:
    """모델·adapter·입력 변경을 모두 포함하는 결정적 TTS cache material."""
    engine = config.get("engine", "supertonic")
    reference = config.get("reference") or {}
    reference_hashes = {}
    for key in ("audio", "transcript"):
        path = reference.get(key)
        if path:
            reference_hashes[key] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    payload = {
        "ttsCacheSchemaVersion": TTS_CACHE_SCHEMA_VERSION,
        "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "engine": engine,
        "model": ENGINE_MODEL_IDS.get(engine),
        "modelRevision": MODEL_REVISIONS.get(engine),
        "package": ENGINE_PACKAGES.get(engine),
        "adapterVersion": ADAPTER_VERSION,
        "normalizerVersion": NORMALIZER_VERSION,
        "voice": config.get("voice"),
        "language": config.get("language", "ko"),
        "speed": config.get("speed", 1.05),
        "pauseMs": config.get("pauseMs", 300),
        "timing": config.get("timing", "tts"),
        "referenceSha256": reference_hashes,
    }
    return payload


def tts_cache_signature(text: str, config: dict[str, Any]) -> str:
    payload = cache_signature_material(text, config)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()

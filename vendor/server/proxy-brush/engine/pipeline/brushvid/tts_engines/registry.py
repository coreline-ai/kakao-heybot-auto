"""TTS 엔진 ID와 adapter factory의 단일 registry."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import TtsEngineError, TtsEngineProtocol


EngineFactory = Callable[..., TtsEngineProtocol]
_FACTORIES: dict[str, EngineFactory] = {}


def register_engine(engine_id: str, factory: EngineFactory) -> None:
    if not isinstance(engine_id, str) or not engine_id.strip():
        raise TtsEngineError("engine ID는 비어 있지 않은 문자열이어야 함")
    if engine_id in _FACTORIES:
        raise TtsEngineError(f"TTS engine 중복 등록: {engine_id}")
    _FACTORIES[engine_id] = factory


def supported_engines() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def create_engine(engine_id: str, **kwargs: Any) -> TtsEngineProtocol:
    try:
        factory = _FACTORIES[engine_id]
    except KeyError as exc:
        allowed = ", ".join(supported_engines()) or "<none>"
        raise TtsEngineError(f"등록되지 않은 TTS engine: {engine_id!r} (등록: {allowed})") from exc
    return factory(**kwargs)

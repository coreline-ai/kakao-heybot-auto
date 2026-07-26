"""공통 TTS adapter 계약.

각 엔진은 native sample rate의 1차원 float waveform을 반환하고, 공통
오케스트레이터가 이후 속도·정규화·pause·SRT를 책임진다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import numpy as np


CLIP_EPSILON = 1e-6


class TtsEngineError(RuntimeError):
    """TTS adapter가 설치·입력·모델·출력 계약을 만족하지 못할 때의 오류."""


class TtsEngineUnavailableError(TtsEngineError):
    """선택한 엔진의 패키지 또는 pinned 모델이 준비되지 않은 상태."""


def validate_audio_samples(
    samples: Any,
    *,
    stage: str = "native",
    clip_epsilon: float = CLIP_EPSILON,
) -> tuple[np.ndarray, int]:
    """1-D finite waveform을 검증하고 epsilon 안의 초과만 clamp한다."""
    try:
        array = np.asarray(samples, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise TtsEngineError(f"{stage} 오디오를 float waveform으로 변환할 수 없음") from exc
    if array.ndim != 1:
        raise TtsEngineError(f"{stage} 오디오는 1차원이어야 함: shape={array.shape}")
    if array.size == 0:
        raise TtsEngineError(f"{stage} 오디오가 비어 있음")
    if not np.isfinite(array).all():
        raise TtsEngineError(f"{stage} 오디오에 NaN 또는 infinity가 있음")
    maximum = float(np.max(np.abs(array)))
    if maximum > 1.0 + clip_epsilon:
        raise TtsEngineError(f"{stage} 오디오 범위 초과: maxAbs={maximum:.8f}")
    clamp_count = int(np.count_nonzero(np.abs(array) > 1.0))
    if clamp_count:
        array = np.clip(array, -1.0, 1.0)
    return array, clamp_count


@dataclass(frozen=True)
class AudioResult:
    """엔진이 반환하는 native waveform과 재현 메타데이터."""

    samples: np.ndarray
    sample_rate: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.sample_rate, bool) or not isinstance(self.sample_rate, int):
            raise TtsEngineError("audio sample_rate는 정수여야 함")
        if self.sample_rate <= 0:
            raise TtsEngineError(f"audio sample_rate가 유효하지 않음: {self.sample_rate}")
        if not isinstance(self.metadata, Mapping):
            raise TtsEngineError("audio metadata는 매핑이어야 함")
        normalized, clamp_count = validate_audio_samples(self.samples)
        metadata = dict(self.metadata)
        metadata.setdefault("nativeClampCount", clamp_count)
        object.__setattr__(self, "samples", normalized)
        object.__setattr__(self, "metadata", metadata)


class TtsEngineProtocol(Protocol):
    """문장 하나를 native waveform으로 합성하는 adapter protocol."""

    engine_id: str

    def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        speed: float,
        reference: Mapping[str, Any] | None = None,
    ) -> AudioResult:
        ...

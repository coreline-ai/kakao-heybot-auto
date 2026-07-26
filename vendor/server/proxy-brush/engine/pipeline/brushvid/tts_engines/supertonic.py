"""기존 Supertonic voice catalog을 보존하는 adapter."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from .base import AudioResult


class SupertonicAdapter:
    engine_id = "supertonic"

    def __init__(
        self,
        *,
        voice: str,
        language: str,
        speed: float,
        importer: Callable[[], Any],
        catalog_loader: Callable[[], dict],
        style_builder: Callable[..., tuple[Any, dict]],
    ) -> None:
        supertonic = importer()
        catalog = catalog_loader()
        expected_package = catalog["engine"]["packageVersion"]
        actual_package = getattr(supertonic, "__version__", "unknown")
        if actual_package != expected_package:
            raise RuntimeError(
                f"Supertonic 버전 불일치: voice pack은 {expected_package}, 설치 버전은 {actual_package}. "
                f'설치: pipeline/.venv/bin/pip install "supertonic=={expected_package}"'
            )
        tts = supertonic.TTS(auto_download=True)
        style, metadata = style_builder(tts, voice, catalog=catalog)
        self._tts = tts
        self._style = style
        self._language = language
        self._speed = speed
        self.metadata = {
            **metadata,
            "engine": self.engine_id,
            "packageVersion": actual_package,
            "model": getattr(tts, "model_name", catalog["engine"]["model"]),
            "language": language,
            "sampleRate": int(getattr(tts, "sample_rate", 44100)),
            "speed": speed,
        }

    def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        speed: float,
        reference=None,
    ) -> AudioResult:
        del voice, reference
        wav, _duration = self._tts.synthesize(
            text,
            voice_style=self._style,
            lang=language,
            speed=speed,
        )
        return AudioResult(
            np.asarray(wav, dtype=np.float32).reshape(-1),
            int(self.metadata["sampleRate"]),
            self.metadata,
        )

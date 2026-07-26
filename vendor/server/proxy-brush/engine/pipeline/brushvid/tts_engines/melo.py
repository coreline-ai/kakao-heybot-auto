"""MeloTTS-Korean pinned local snapshot adapter."""
from __future__ import annotations

import importlib.metadata
import importlib.machinery
import sys
from types import ModuleType
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ..tts_contract import (
    ENGINE_LICENSES,
    ENGINE_MODEL_IDS,
    MODEL_REVISIONS,
    resolve_local_snapshot,
)
from .base import AudioResult, TtsEngineUnavailableError


MODEL_FILES = ("config.json", "checkpoint.pth")


def _ensure_korean_mecab() -> None:
    """Melo import가 요구하는 대문자 MeCab 모듈을 안전하게 제공한다.

    한국어 추론은 `python-mecab-ko`의 소문자 `mecab` 및 g2pkk를 그대로
    사용한다. Melo는 모든 언어 모듈을 한 번에 import하면서 비사용 일본어
    모듈에도 `MeCab.Tagger()`를 요구한다. macOS의 lower-case 한국어 패키지
    환경에서는 그 import만 호환시키는 shim을 등록한다. 한국어 tokenizer,
    G2P, BERT embedding이나 모델 출력에는 개입하지 않는다.
    """
    try:
        import MeCab  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    try:
        import mecab
    except ImportError as exc:
        raise TtsEngineUnavailableError("python-mecab-ko가 필요함") from exc

    class Tagger:
        def __init__(self, *_args, **_kwargs):
            self._mecab = mecab.MeCab()

        def parse(self, sentence: str) -> str:
            # 이 메서드는 한국어 경로에서 호출되지 않는다. 명시적으로 잘못된
            # 일본어 입력을 합성하려 하면 오류를 내어 조용한 품질 저하를 막는다.
            raise RuntimeError(
                "이 Melo runtime은 Korean-only이며 Japanese MeCab parsing을 지원하지 않음"
            )

    module = ModuleType("MeCab")
    module.__spec__ = importlib.machinery.ModuleSpec("MeCab", loader=None)
    module.Tagger = Tagger
    sys.modules["MeCab"] = module


class MeloAdapter:
    engine_id = "melo-ko"

    def __init__(
        self,
        *,
        model_dir: str | Path | None = None,
        device: str = "cpu",
        tts_factory: Callable[..., Any] | None = None,
    ) -> None:
        try:
            if tts_factory is None:
                _ensure_korean_mecab()
                from melo.api import TTS
                tts_factory = TTS
        except (ImportError, RuntimeError) as exc:
            raise TtsEngineUnavailableError(
                "melo-ko 환경을 사용할 수 없음. 설치: pipeline/.venv/bin/pip install -e 'pipeline[tts-melo]'"
            ) from exc
        snapshot = resolve_local_snapshot(
            ENGINE_MODEL_IDS[self.engine_id],
            MODEL_REVISIONS[self.engine_id],
            explicit_dir=model_dir,
            required_files=MODEL_FILES,
        )
        try:
            model = tts_factory(
                language="KR",
                device=device,
                use_hf=False,
                config_path=str(snapshot / "config.json"),
                ckpt_path=str(snapshot / "checkpoint.pth"),
            )
        except Exception as exc:
            raise TtsEngineUnavailableError(f"melo-ko pinned model 로드 실패: {exc}") from exc
        speakers = getattr(getattr(getattr(model, "hps", None), "data", None), "spk2id", {})
        if "KR" not in speakers:
            raise RuntimeError(f"melo-ko speaker KR 없음; 임의 fallback 금지: {sorted(speakers)}")
        if bool(getattr(model.hps.data, "disable_bert", False)):
            raise RuntimeError("QUALITY_GATE_FAILED: melo-ko Korean contextual BERT가 비활성화됨")
        self._model = model
        self._speaker_id = speakers["KR"]
        self._sample_rate = int(model.hps.data.sampling_rate)
        try:
            package_version = importlib.metadata.version("melotts")
        except importlib.metadata.PackageNotFoundError:
            package_version = "0.1.2"
        self.metadata = {
            "engine": self.engine_id,
            "model": ENGINE_MODEL_IDS[self.engine_id],
            "modelRevision": MODEL_REVISIONS[self.engine_id],
            "packageVersion": package_version,
            "language": "ko",
            "speaker": "KR",
            "nativeSampleRate": self._sample_rate,
            "contextualBert": "kykim/bert-kor-base enabled",
            "speedAppliedBy": "melo-native-length-scale",
            "license": {**ENGINE_LICENSES[self.engine_id], "aiDisclosureRequired": True},
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
        if voice != "kr-default":
            raise ValueError(f"melo-ko voice는 kr-default만 지원함: {voice!r}")
        if language != "ko":
            raise ValueError(f"melo-ko language는 ko만 지원함: {language!r}")
        if reference is not None:
            raise ValueError("melo-ko는 reference를 지원하지 않음")
        samples = self._model.tts_to_file(
            text,
            self._speaker_id,
            output_path=None,
            speed=float(speed),
            quiet=True,
        )
        return AudioResult(np.asarray(samples, dtype=np.float32).reshape(-1), self._sample_rate, self.metadata)

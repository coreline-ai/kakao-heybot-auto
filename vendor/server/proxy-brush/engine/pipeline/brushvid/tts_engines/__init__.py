"""Engine-neutral TTS contracts and adapter registry."""

from .base import AudioResult, TtsEngineError, TtsEngineProtocol
from .melo import MeloAdapter
from .supertonic import SupertonicAdapter

__all__ = [
    "AudioResult", "MeloAdapter", "SupertonicAdapter", "TtsEngineError", "TtsEngineProtocol",
]

"""tts.py — Supertonic TTS: 대본/자막 텍스트 → 더빙 wav + 타이밍 SRT.

한국어 문장부호로 분리 → 문장별 합성 → pauseMs 무음 삽입 연결.
타이밍의 시계는 **합성된 각 wav 의 실제 샘플 길이** — 문장 길이 합 + pause 합 =
최종 wav 길이 = SRT 마지막 타임스탬프가 항상 정합한다.

supertonic 미설치 시 침묵 폴백 없이 설치 명령을 담은 명확한 에러를 낸다.
"""
from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from .voice_presets import (
    SPEED_MAX,
    SPEED_MIN,
    build_voice_style,
    load_catalog,
    resolve_voice,
)
from .tts_contract import ENGINE_IDS, validate_speed
from .tts_engines.melo import MeloAdapter
from .tts_engines.qwen import QwenAdapter
from .tts_engines.base import AudioResult, validate_audio_samples
from .tts_engines.registry import create_engine, register_engine, supported_engines
from .tts_engines.supertonic import SupertonicAdapter

log = logging.getLogger(__name__)

SR = 44100  # supertonic 출력 샘플레이트
INSTALL_HINT = 'TTS 사용을 위해 supertonic 이 필요합니다. 설치: pipeline/.venv/bin/pip install -e "pipeline[tts]"'

_SENT_RE = re.compile(r"[^.!?。…]+[.!?。…]*")


def split_sentences(text: str) -> list[str]:
    """한국어 문장부호(.!?…) 기준 문장 분리. 빈 조각은 제거."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for m in _SENT_RE.finditer(line):
            s = m.group().strip()
            if s:
                out.append(s)
    return out


def _import_supertonic():
    """supertonic 임포트 — 미설치 시 설치 명령 포함 에러 (침묵 폴백 금지)."""
    try:
        import supertonic
        return supertonic
    except ImportError as e:
        raise RuntimeError(INSTALL_HINT) from e


def _make_synthesizer(voice: str, lang: str, speed: float):
    """문장 합성 함수와 실제 해석된 voice metadata를 생성."""
    adapter = create_engine(
        "supertonic",
        voice=voice,
        language=lang,
        speed=speed,
    )

    def synth(text: str) -> np.ndarray:
        return adapter.synthesize(
            text, voice=voice, language=lang, speed=speed,
        ).samples

    return synth, dict(adapter.metadata)


def _make_melo_synthesizer(voice: str, lang: str, speed: float):
    """MeloTTS-Korean adapter와 native output metadata를 생성한다."""
    adapter = create_engine("melo-ko")

    def synth(text: str) -> np.ndarray:
        return adapter.synthesize(
            text, voice=voice, language=lang, speed=speed,
        ).samples

    return synth, dict(adapter.metadata, speed=speed, sampleRate=adapter.metadata["nativeSampleRate"])


def _apply_atempo(samples: np.ndarray, sample_rate: int, speed: float) -> np.ndarray:
    """Qwen처럼 native speed를 지원하지 않는 출력에 ffmpeg atempo를 적용한다."""
    if abs(speed - 1.0) < 1e-9:
        return samples
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Qwen speed 적용에 ffmpeg가 필요함")
    with tempfile.TemporaryDirectory(prefix="brushvid-atempo-") as temp_dir:
        source = Path(temp_dir) / "source.wav"
        target = Path(temp_dir) / "target.wav"
        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(str(source), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(sample_rate))
            wav_file.writeframes(pcm16.tobytes())
        try:
            subprocess.run(
                [ffmpeg, "-v", "error", "-y", "-i", str(source),
                 "-af", f"atempo={speed:.9g}", "-ar", str(sample_rate), "-ac", "1",
                 "-c:a", "pcm_s16le", str(target)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=120,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise RuntimeError(f"Qwen atempo 적용 실패: {detail}") from exc
        with wave.open(str(target), "rb") as wav_file:
            if wav_file.getnchannels() != 1 or wav_file.getframerate() != int(sample_rate):
                raise RuntimeError("Qwen atempo 출력이 mono/native sample rate가 아님")
            output = np.frombuffer(wav_file.readframes(wav_file.getnframes()), dtype="<i2")
        return output.astype(np.float32) / 32767.0


def _normalize_audio(
    result: AudioResult | np.ndarray,
    sample_rate: int = SR,
    *,
    speed: float = 1.0,
    apply_speed: bool = False,
) -> np.ndarray:
    """native adapter waveform을 Qwen speed 처리 후 44.1kHz mono로 정규화한다."""
    if isinstance(result, AudioResult):
        samples, native_rate = result.samples, result.sample_rate
    else:
        samples, native_rate = result, sample_rate
    samples, _clamp_count = validate_audio_samples(samples, stage="TTS")
    if apply_speed:
        samples = _apply_atempo(samples, int(native_rate), speed)
        samples, _clamp_count = validate_audio_samples(samples, stage="Qwen atempo")
    if native_rate == SR:
        return samples
    from math import gcd
    factor = gcd(int(native_rate), SR)
    normalized = resample_poly(samples, SR // factor, int(native_rate) // factor).astype(np.float32)
    normalized, _ = validate_audio_samples(normalized, stage="정규화 TTS")
    return normalized


def _register_builtin_engines() -> None:
    """모든 runtime 엔진을 단일 registry에 등록한다; 모델은 factory 호출 시에만 로드한다."""
    if "supertonic" not in supported_engines():
        register_engine(
            "supertonic",
            lambda **kwargs: SupertonicAdapter(
                **kwargs,
                importer=_import_supertonic,
                catalog_loader=load_catalog,
                style_builder=build_voice_style,
            ),
        )
    if "melo-ko" not in supported_engines():
        register_engine("melo-ko", lambda **kwargs: MeloAdapter(**kwargs))
    if "qwen3-base" not in supported_engines():
        register_engine("qwen3-base", lambda **kwargs: QwenAdapter(**kwargs))


_register_builtin_engines()


def format_srt_time(sec: float) -> str:
    ms = round(sec * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def synthesize_narration(text: str, out_wav: str | Path, out_srt: str | Path, *,
                         engine: str = "supertonic", voice: str = "F1", pause_ms: int = 300,
                         speed: float = 1.05, lang: str = "ko", reference=None,
                         work_root: str | Path | None = None, synth=None) -> dict:
    """대본 텍스트 → 더빙 wav + SRT. 반환: {wav, srt, entries, durationSec}.

    synth 는 테스트 주입용 (문장 → 1D float 배열). 미지정 시 supertonic 사용.
    """
    if engine not in ENGINE_IDS:
        raise ValueError(f"지원하지 않는 TTS 엔진: {engine!r}")
    try:
        speed = validate_speed(speed, minimum=SPEED_MIN, maximum=SPEED_MAX)
    except ValueError as exc:
        raise ValueError(str(exc).replace("input.tts.", "TTS ")) from exc
    sentences = split_sentences(text)
    if not sentences:
        raise ValueError("TTS 입력 텍스트에 문장이 없음")
    batch_results: list[AudioResult] | None = None
    if synth is None and engine == "qwen3-base":
        if reference is None:
            raise ValueError("qwen3-base는 명시적 reference audio/transcript가 필요함")
        adapter = create_engine("qwen3-base", reference=reference, work_root=work_root)
        batch_results = adapter.synthesize_batch(
            sentences, voice=voice, language=lang, speed=speed,
        )
        voice_metadata = dict(batch_results[0].metadata)
        voice_metadata["speedAppliedBy"] = "ffmpeg-atempo"
        voice_metadata["sampleRate"] = SR
        voice_metadata["outputSampleRate"] = SR
        synth = None
    elif synth is None:
        if engine == "supertonic":
            synth, voice_metadata = _make_synthesizer(voice, lang, speed)
        elif engine == "melo-ko":
            synth, voice_metadata = _make_melo_synthesizer(voice, lang, speed)
    else:
        # 테스트/외부 주입 합성기도 ID 계약은 동일하게 검증한다.
        if engine == "supertonic":
            catalog = load_catalog()
            voice_metadata = resolve_voice(voice, catalog)
            voice_metadata.update({
                "engine": engine,
                "packageVersion": "injected-synth",
                "model": catalog["engine"]["model"],
                "language": lang,
                "sampleRate": SR,
                "speed": speed,
                "styleSourceSha256": {
                    name: catalog["baseStyleSha256"].get(name)
                    for name in voice_metadata["components"]
                },
                "styleSourceKind": "catalog-expected",
                "styleSha256": None,
            })
        else:
            voice_metadata = {
                "engine": engine, "requestedVoice": voice, "voice": voice,
                "packageVersion": "injected-synth", "model": engine,
                "language": lang, "sampleRate": SR, "speed": speed,
            }

    pause = np.zeros(int(SR * pause_ms / 1000), dtype=np.float32)
    parts: list[np.ndarray] = []
    entries: list[dict] = []
    t = 0.0
    for i, s in enumerate(sentences):
        raw_segment = batch_results[i] if batch_results is not None else synth(s)
        seg = _normalize_audio(
            raw_segment, SR, speed=speed,
            apply_speed=engine == "qwen3-base",
        )
        dur = len(seg) / SR  # 실제 샘플 길이가 타이밍의 시계
        entries.append({"index": i + 1, "start": t, "end": t + dur, "text": s})
        parts.append(seg)
        t += dur
        if i < len(sentences) - 1:
            parts.append(pause)
            t += len(pause) / SR
        log.info("tts %d/%d: %.2fs — %s", i + 1, len(sentences), dur, s[:32])

    audio = np.concatenate(parts)
    out_wav, out_srt = Path(out_wav), Path(out_srt)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())

    srt_text = "\n".join(
        f"{e['index']}\n{format_srt_time(e['start'])} --> {format_srt_time(e['end'])}\n{e['text']}\n"
        for e in entries)
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    out_srt.write_text(srt_text, encoding="utf-8")
    total = len(audio) / SR
    log.info("tts 완료: 문장 %d개, %.2fs -> %s / %s", len(sentences), total, out_wav, out_srt)
    return {
        "wav": str(out_wav), "srt": str(out_srt), "entries": entries,
        "durationSec": total, "voice": voice_metadata,
    }

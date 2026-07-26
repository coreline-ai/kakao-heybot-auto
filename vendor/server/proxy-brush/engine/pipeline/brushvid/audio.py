"""audio.py — 앰비언트 BGM 합성(numpy/wave) + 오디오-씬 길이 정합.

BGM 레시피는 참조 빌더(felt-piano 아르페지오 + 스트링 패드 + 벨, 에코/로우패스)를
임의 길이로 일반화해 채택했다. 시드 고정 결정적.
"""
from __future__ import annotations

import logging
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
from scipy.signal import lfilter

log = logging.getLogger(__name__)

SR = 48000

# 잔잔한 코드 진행 (Dadd9 → Bm7 → G6 → A) — 세그먼트당 ~9.6초 루프
_CHORDS = [
    [50, 57, 62, 64, 69],
    [47, 54, 57, 62, 66],
    [43, 50, 55, 59, 62],
    [45, 52, 57, 61, 64],
]
_SEG = 9.6


def _midi_to_freq(m: int) -> float:
    return 440.0 * (2 ** ((m - 69) / 12))


def _adsr(length: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    env = np.zeros(length)
    a, d, r = (max(1, int(v * SR)) for v in (attack, decay, release))
    s_len = max(0, length - a - d - r)
    pos = 0
    env[pos:pos + a] = np.linspace(0, 1, min(a, length - pos), endpoint=False)
    pos += a
    if pos < length:
        env[pos:pos + d] = np.linspace(1, sustain, min(d, length - pos), endpoint=False)
        pos += d
    if s_len and pos < length:
        env[pos:pos + s_len] = sustain
        pos += s_len
    if pos < length:
        env[pos:] = np.linspace(env[pos - 1] if pos else sustain, 0, length - pos, endpoint=False)
    return env


def _add_tone(buf: np.ndarray, start: float, midi: int, dur: float, amp: float,
              env: np.ndarray | None, harmonics: list[tuple[float, float]], pan: float) -> None:
    n_total = buf.shape[0]
    idx = int(start * SR)
    length = min(int(dur * SR), n_total - idx)
    if length <= 0:
        return
    x = np.arange(length) / SR
    f = _midi_to_freq(midi)
    tone = np.zeros(length)
    for h, a in harmonics:
        tone += a * np.sin(2 * math.pi * f * h * x + 0.07 * h)
    if env is None:
        env = _adsr(length, 0.012, 0.55, 0.20, min(1.8, dur * 0.55))
    tone *= env[:length] * amp
    buf[idx:idx + length, 0] += tone * math.cos(pan * math.pi / 2)
    buf[idx:idx + length, 1] += tone * math.sin(pan * math.pi / 2)


def synth_ambient_bgm(out_path: str | Path, duration_sec: float, seed: int = 1) -> Path:
    """임의 길이 앰비언트 BGM(wav, 48kHz 스테레오) 합성. 시드 고정 결정적."""
    rng = np.random.default_rng(seed)
    n = int(SR * duration_sec)
    buf = np.zeros((n, 2))

    seg_count = max(1, math.ceil(duration_sec / _SEG))
    piano_harm = [(1, 1.0), (2, 0.38), (3, 0.18), (4, 0.08), (1.006, 0.18)]
    bell_harm = [(1, 1.0), (2.01, 0.35), (3.02, 0.18)]

    for si in range(seg_count):
        st = si * _SEG
        chord = _CHORDS[si % len(_CHORDS)]
        # 패드: 코드 전체를 길게
        pad_len = min(_SEG + 1.2, duration_sec - st)
        if pad_len <= 0:
            break
        env = _adsr(int(pad_len * SR), 1.6, 1.8, 0.82, 2.6)
        for i, m in enumerate(chord):
            _add_tone(buf, st, m, pad_len, 0.020 / math.sqrt(len(chord)), env,
                      [(1, 1.0), (2, 0.45)], 0.35 + 0.30 * i / max(1, len(chord) - 1))
        # felt-piano 아르페지오 (성긴 8분)
        arp = chord + chord[-2::-1]
        base = st + float(rng.uniform(0.5, 1.1))
        for j, m in enumerate(arp):
            vel = 0.074 if j in (0, 4) else 0.058
            _add_tone(buf, base + j * 0.48, m + 12, 2.4, vel, None, piano_harm,
                      0.42 + 0.14 * ((j % 3) / 2))
        # 벨 1회 (세그먼트 전환 지점)
        bell_m = chord[-1] + 24
        bell_len = min(2.6, duration_sec - (st + _SEG * 0.4))
        if bell_len > 0.5:
            x = np.arange(int(bell_len * SR)) / SR
            bell_env = np.exp(-x * 1.9) * (1 - np.exp(-x * 35))
            _add_tone(buf, st + _SEG * 0.4, bell_m, bell_len, 0.014, bell_env,
                      bell_harm, float(rng.uniform(0.42, 0.58)))

    # 에코 2탭 + 원-폴 로우패스 (lfilter 로 벡터화)
    for delay_s, wet in ((0.34, 0.18), (0.62, 0.10)):
        d = int(delay_s * SR)
        if d < n:
            buf[d:] += buf[:-d] * wet
    alpha = 0.12
    wet = lfilter([alpha], [1.0, -(1.0 - alpha)], buf, axis=0)
    buf = buf * 0.72 + wet * 0.28

    # 페이드 인/아웃 + 소프트 클립 + 노멀라이즈
    t = np.arange(n) / SR
    buf *= np.minimum(np.clip(t / 1.2, 0, 1), np.clip((duration_sec - t) / 2.4, 0, 1))[:, None]
    buf = np.tanh(buf * 1.15)
    peak = float(np.max(np.abs(buf))) or 1.0
    pcm16 = (np.clip(buf / peak * 0.24, -0.98, 0.98) * 32767).astype("<i2")

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    log.info("BGM 합성 %.1fs (seed=%d) -> %s", duration_sec, seed, out)
    return out


def create_silent_audio(out_path: str | Path, duration_sec: float) -> Path:
    """전달 규격용 무음 PCM WAV를 스트리밍 생성한다.

    BGM과 내레이션이 모두 없는 영상도 H.264/AAC MP4 계약을 유지하기 위해 사용한다.
    메모리에 10분 전체 버퍼를 올리지 않고 일정 크기의 48 kHz 스테레오 무음 블록을
    반복 기록한다.
    """
    if duration_sec <= 0:
        raise ValueError("무음 오디오 길이는 0보다 커야 함")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames_left = round(SR * duration_sec)
    block_frames = 96_000
    block = b"\x00\x00\x00\x00" * block_frames  # 16-bit stereo
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SR)
        while frames_left:
            count = min(block_frames, frames_left)
            wav.writeframes(block if count == block_frames else b"\x00\x00\x00\x00" * count)
            frames_left -= count
    log.info("무음 오디오 %.1fs -> %s", duration_sec, out)
    return out


def probe_duration(media_path: str | Path) -> float:
    """ffprobe 로 미디어 길이(초)."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
        capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def reconcile_scenes_with_audio(scenes: list[dict], audio_sec: float, fps: int = 30,
                                tolerance_sec: float = 1.0) -> bool:
    """씬 합산 길이와 오디오 길이가 tolerance 초과로 다르면 경고 + 마지막 씬을 보정.

    반환: 보정 수행 여부. scenes 는 in-place 수정.
    """
    total = sum(s["durationInFrames"] for s in scenes)
    audio_frames = round(audio_sec * fps)
    diff = audio_frames - total
    if abs(diff) / fps <= tolerance_sec:
        return False
    last = scenes[-1]
    min_last = (last["cues"][-1]["to"] if last.get("cues") else 0) + 6
    new_last = max(min_last, last["durationInFrames"] + diff)
    log.warning("오디오 %.2fs vs 씬 합산 %.2fs — 마지막 씬 %d→%d 프레임 자동 보정",
                audio_sec, total / fps, last["durationInFrames"], new_last)
    last["durationInFrames"] = new_last
    return True

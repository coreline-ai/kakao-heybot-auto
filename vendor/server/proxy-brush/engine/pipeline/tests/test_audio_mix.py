import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from brushvid.mix import (measure_ducking_regions, measure_loudness, mix_voice_and_bgm,
                          prepare_bgm, probe_duration)


def _tone(path: Path, *, seconds: float, frequency: int, volume: float = 0.2):
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        f"sine=frequency={frequency}:duration={seconds}", "-af", f"volume={volume}",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(path),
    ], check=True)


def test_single_bgm_normalizes_to_ambient_target(tmp_path):
    src = tmp_path / "quiet.wav"
    _tone(src, seconds=5, frequency=330, volume=0.03)
    out, report = prepare_bgm(
        [src], tmp_path / "bgm.wav", duration_sec=4, work_dir=tmp_path / "work",
        gain_db=5, fade_in_sec=0.2, fade_out_sec=0.2,
    )
    loud = measure_loudness(out, duration_sec=4)
    assert probe_duration(out) == pytest.approx(4, abs=0.05)
    assert loud["integratedLufs"] == pytest.approx(-18, abs=1.2)
    assert loud["truePeakDbtp"] <= -1.0
    assert report["kind"] == "asset"


def test_subsecond_bgm_shrinks_overlapping_fades_without_silence(tmp_path):
    src = tmp_path / "short-source.wav"
    _tone(src, seconds=1, frequency=330, volume=0.1)
    out, _report = prepare_bgm(
        [src], tmp_path / "short-bgm.wav", duration_sec=0.6,
        work_dir=tmp_path / "short-work", gain_db=5,
        fade_in_sec=2.0, fade_out_sec=2.0,
    )
    loud = measure_loudness(out, duration_sec=0.6)
    assert probe_duration(out) == pytest.approx(0.6, abs=0.05)
    assert loud["integratedLufs"] > -60
    assert loud["truePeakDbtp"] <= -1.0


def test_source_start_removes_leading_silence_before_fade(tmp_path):
    src = tmp_path / "silent-then-tone.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=stereo:d=2", "-f", "lavfi", "-i",
        "sine=frequency=330:duration=3", "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(src),
    ], check=True)
    out, report = prepare_bgm(
        [src], tmp_path / "trimmed-bgm.wav", duration_sec=2,
        work_dir=tmp_path / "trim-work", gain_db=0,
        fade_in_sec=0.1, fade_out_sec=0.1, source_start_sec=2.0,
    )
    raw = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", "0.2", "-t", "0.3", "-i", str(out),
        "-f", "f32le", "-ac", "1", "-ar", "48000", "pipe:1",
    ], capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    assert np.sqrt(np.mean(samples * samples)) > 0.01
    assert report["tracks"][0]["sourceStartSec"] == 2.0


def test_playlist_crossfade_has_exact_duration(tmp_path):
    a, b, c = (tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav")
    _tone(a, seconds=4, frequency=220)
    _tone(b, seconds=4, frequency=330)
    _tone(c, seconds=4, frequency=440)
    out, report = prepare_bgm(
        [a, b, c], tmp_path / "playlist.wav", duration_sec=6,
        work_dir=tmp_path / "work", gain_db=5, fade_in_sec=0.1,
        fade_out_sec=0.1, crossfade_sec=0.5,
    )
    assert probe_duration(out) == pytest.approx(6, abs=0.05)
    assert report["kind"] == "playlist"
    assert report["crossfadeSec"] == pytest.approx(0.5)
    assert report["sequenceAssetIndexes"] == [0, 1, 2]
    assert len(report["transitionTimesSec"]) == 2


def test_short_playlist_accepts_duplicate_track_and_repeats_whole_sequence(tmp_path):
    a = tmp_path / "a.wav"
    _tone(a, seconds=1, frequency=220)
    out, report = prepare_bgm(
        [a, a], tmp_path / "playlist.wav", duration_sec=5,
        work_dir=tmp_path / "work", gain_db=5, fade_in_sec=0.1,
        fade_out_sec=0.1, crossfade_sec=0.2,
    )
    assert probe_duration(out) == pytest.approx(5, abs=0.05)
    assert report["sequenceAssetIndexes"][:4] == [0, 1, 0, 1]
    assert len(report["sequenceAssetIndexes"]) > 2
    assert len(report["transitionTimesSec"]) == len(report["sequenceAssetIndexes"]) - 1
    assert all(b > a for a, b in zip(report["transitionTimesSec"],
                                    report["transitionTimesSec"][1:]))


def test_voice_and_bgm_master_target_and_ducking_report(tmp_path):
    bgm_src, voice = tmp_path / "bgm-src.wav", tmp_path / "voice.wav"
    _tone(bgm_src, seconds=6, frequency=220, volume=0.1)
    _tone(voice, seconds=6, frequency=660, volume=0.2)
    bgm, _ = prepare_bgm(
        [bgm_src], tmp_path / "bgm.wav", duration_sec=6, work_dir=tmp_path / "bgm-work",
        gain_db=3, fade_in_sec=0.1, fade_out_sec=0.1,
    )
    master, report = mix_voice_and_bgm(
        voice, bgm, tmp_path / "master.wav", duration_sec=6,
        ducking_enabled=True, ducking_amount_db=8, attack_ms=120, release_ms=600,
        work_dir=tmp_path / "mix-work",
    )
    loud = measure_loudness(master, duration_sec=6)
    assert loud["integratedLufs"] == pytest.approx(-16, abs=1.0)
    assert loud["truePeakDbtp"] <= -1.0
    assert report["ducking"]["enabled"] is True
    assert report["ducking"]["ratio"] > 1
    assert 0 < report["ducking"]["wetMix"] < 1
    assert report["ducking"]["measuredAttenuationDb"] >= 1
    assert Path(report["ducking"]["stemPath"]).is_file()
    assert (report["ducking"]["bgmBefore"]["integratedLufs"]
            > report["ducking"]["bgmAfter"]["integratedLufs"])


def test_ducking_region_metrics_show_attenuation_and_recovery(tmp_path):
    bgm, voice, ducked = tmp_path / "bgm.wav", tmp_path / "voice.wav", tmp_path / "ducked.wav"
    _tone(bgm, seconds=6, frequency=220, volume=0.15)
    # 0~2초 음성, 2~4초 무음, 4~6초 음성으로 복귀 구간을 명시한다.
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=660:duration=6", "-af",
        "volume='if(between(t,2,4),0,1.0)':eval=frame", "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le", str(voice),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(bgm), "-i", str(voice),
        "-filter_complex",
        "[0:a][1:a]sidechaincompress=threshold=0.025:ratio=8.2:attack=20:release=100[out]",
        "-map", "[out]", "-c:a", "pcm_s16le", str(ducked),
    ], check=True)
    metrics = measure_ducking_regions(voice, bgm, ducked, duration_sec=6)
    assert metrics is not None
    assert metrics["activeAttenuationDb"] >= 1
    assert metrics["inactiveAttenuationDb"] < metrics["activeAttenuationDb"] - 1


def _window_rms(path: Path, start: float, end: float) -> float:
    with wave.open(str(path)) as wav:
        channels, rate = wav.getnchannels(), wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    mono = samples.reshape(-1, channels)[:, 0].astype(np.float64) / 32768.0
    section = mono[int(start * rate):int(end * rate)]
    return float(np.sqrt(np.mean(section * section)))


def _max_sample_step(path: Path, start: float, end: float) -> float:
    with wave.open(str(path)) as wav:
        channels, rate = wav.getnchannels(), wav.getframerate()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    mono = samples.reshape(-1, channels)[:, 0].astype(np.float64) / 32768.0
    section = mono[int(start * rate):int(end * rate)]
    return float(np.max(np.abs(np.diff(section))))


def test_real_ducking_reacts_within_attack_recovers_after_release_without_clicks(tmp_path):
    """실제 공통 믹서의 attack/release 시간과 경계 연속성을 PCM에서 확인한다."""
    bgm_src, voice = tmp_path / "bgm-src.wav", tmp_path / "voice-gated.wav"
    _tone(bgm_src, seconds=5, frequency=220, volume=0.12)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "sine=frequency=660:duration=5", "-af",
        "volume='if(between(t,1,3),0.8,0)':eval=frame", "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s16le", str(voice),
    ], check=True)
    bgm, _ = prepare_bgm(
        [bgm_src], tmp_path / "bgm.wav", duration_sec=5, work_dir=tmp_path / "bgm-work",
        gain_db=3, fade_in_sec=0, fade_out_sec=0,
    )
    _master, report = mix_voice_and_bgm(
        voice, bgm, tmp_path / "master.wav", duration_sec=5,
        ducking_enabled=True, ducking_amount_db=8, attack_ms=120, release_ms=600,
        work_dir=tmp_path / "mix-work",
    )
    ducked = Path(report["ducking"]["stemPath"])

    def attenuation(start: float, end: float) -> float:
        return 20 * np.log10(max(_window_rms(bgm, start, end), 1e-9)
                             / max(_window_rms(ducked, start, end), 1e-9))

    assert attenuation(0.5, 0.9) < 0.5
    assert attenuation(1.12, 1.3) >= 1.0  # 요청 attack 120ms 안에 반응
    assert attenuation(1.5, 2.8) >= 2.0
    assert attenuation(3.7, 4.2) < 0.75  # 요청 release 600ms 뒤 복귀
    assert _max_sample_step(ducked, 0.95, 1.2) < 0.03
    assert _max_sample_step(ducked, 2.95, 3.7) < 0.03

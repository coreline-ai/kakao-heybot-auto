"""ffmpeg 기반 BGM 정규화·페이드·플레이리스트·내레이션 덕킹.

모든 중간/최종 WAV는 48kHz stereo PCM으로 고정한다. 외부 음원은 먼저 -23 LUFS
기준으로 정규화하고 앰비언트 +5dB, 내레이션 +3dB 트림을 적용한다.
"""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
from array import array
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SR = 48000
BGM_REFERENCE_LUFS = -23.0
VOICE_TARGET_LUFS = -16.0
TRUE_PEAK_DB = -1.0
LIMIT_LINEAR = 10 ** (TRUE_PEAK_DB / 20.0)


class AudioMixError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    log.info("$ %s", " ".join(str(c) for c in cmd))
    try:
        return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()[-3000:]
        raise AudioMixError(f"ffmpeg 오디오 처리 실패:\n{detail}") from exc


def probe_duration(path: str | Path) -> float:
    res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    return float(res.stdout.strip())


_LOUD_JSON = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


def measure_loudness(path: str | Path, *, duration_sec: float | None = None) -> dict:
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    # 목표 구간이 원본보다 길 때만 loop한다. 같은 길이에서도 -stream_loop를 켜면
    # 컨테이너 끝 경계가 한 번 더 분석되어 Integrated LUFS가 약 1dB 부풀 수 있다.
    if duration_sec is not None and probe_duration(path) + 0.01 < duration_sec:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(path)]
    if duration_sec is not None:
        cmd += ["-t", f"{duration_sec:.6f}"]
    cmd += ["-af", "loudnorm=I=-23:LRA=11:TP=-1.0:print_format=json", "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AudioMixError(f"loudness 측정 실패: {path}\n{exc.stderr[-2000:]}") from exc
    matches = _LOUD_JSON.findall(res.stderr)
    if not matches:
        raise AudioMixError(f"loudnorm JSON 결과 없음: {path}")
    raw = json.loads(matches[-1])

    def number(key: str) -> float:
        value = raw.get(key)
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise AudioMixError(f"loudnorm {key} 값 오류: {value!r}") from exc
        if not math.isfinite(result):
            raise AudioMixError(f"loudnorm {key}가 유한값이 아님: {value!r}")
        return result

    return {
        "integratedLufs": number("input_i"),
        "truePeakDbtp": number("input_tp"),
        "lra": number("input_lra"),
        "threshold": number("input_thresh"),
        "targetOffset": number("target_offset"),
    }


def normalize_track(input_path: str | Path, out_path: str | Path, *, target_lufs: float,
                    duration_sec: float, true_peak: float = TRUE_PEAK_DB) -> tuple[Path, dict]:
    """사용할 구간만 2-pass loudnorm. 원본이 짧으면 결정적으로 loop한다."""
    if duration_sec <= 0:
        raise AudioMixError("normalize duration은 0보다 커야 함")
    measured = measure_loudness(input_path, duration_sec=duration_sec)
    filt = (
        f"loudnorm=I={target_lufs}:LRA=11:TP={true_peak}:"
        f"measured_I={measured['integratedLufs']}:measured_LRA={measured['lra']}:"
        f"measured_TP={measured['truePeakDbtp']}:measured_thresh={measured['threshold']}:"
        f"offset={measured['targetOffset']}:linear=true:print_format=summary,"
        "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo"
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y"]
    if probe_duration(input_path) + 0.01 < duration_sec:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(input_path), "-t", f"{duration_sec:.6f}", "-af", filt, "-ar", str(SR), "-ac", "2",
            "-c:a", "pcm_s16le", str(out)]
    _run(cmd)
    return out, measured


def _fade_filter(duration_sec: float, fade_in_sec: float, fade_out_sec: float) -> list[str]:
    if duration_sec <= 0:
        raise AudioMixError("오디오 길이는 0보다 커야 함")
    # 매우 짧은 영상에서는 두 fade가 겹치지 않게 각각 최대 길이의 45%로 축소한다.
    max_each = duration_sec * 0.45
    fade_in = min(max(0.0, fade_in_sec), max_each)
    fade_out = min(max(0.0, fade_out_sec), max_each)
    filters: list[str] = []
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in:.6f}:curve=qsin")
    if fade_out > 0:
        filters.append(
            f"afade=t=out:st={max(0.0, duration_sec - fade_out):.6f}:d={fade_out:.6f}:curve=qsin"
        )
    return filters


def finish_bgm(input_path: str | Path, out_path: str | Path, *, duration_sec: float,
               gain_db: float, fade_in_sec: float, fade_out_sec: float) -> Path:
    filters = [f"volume={gain_db:.3f}dB", *_fade_filter(duration_sec, fade_in_sec, fade_out_sec),
               f"alimiter=limit={LIMIT_LINEAR:.8f}:level=false",
               "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo"]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-i", str(input_path), "-t", f"{duration_sec:.6f}",
          "-af", ",".join(filters), "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(out)])
    return out


def prepare_bgm(asset_paths: list[str | Path], out_path: str | Path, *, duration_sec: float,
                work_dir: str | Path, gain_db: float, fade_in_sec: float,
                fade_out_sec: float, crossfade_sec: float = 3.0,
                source_start_sec: float = 0.0) -> tuple[Path, dict]:
    """단일곡 또는 2~3곡 playlist를 목표 길이로 준비한다."""
    if not asset_paths:
        raise AudioMixError("BGM asset 경로가 비어 있음")
    if source_start_sec < 0:
        raise AudioMixError("BGM source start는 0 이상이어야 함")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    count = len(asset_paths)
    crossfade = min(crossfade_sec, max(0.0, duration_sec / max(2, count * 3))) if count > 1 else 0.0
    source_durations = [probe_duration(source) - source_start_sec for source in asset_paths]
    if any(value <= 0.01 for value in source_durations):
        raise AudioMixError("BGM source start가 원본 음원 길이보다 큼")
    desired_segment_sec = (duration_sec + crossfade * (count - 1)) / count
    # 단일곡은 목표 길이까지 loop한다. playlist는 원본 한 곡을 반복하지 않고
    # 각 곡의 실제 길이까지만 사용한 뒤 목록 전체 순서를 반복한다.
    segment_secs = ([duration_sec] if count == 1 else
                    [min(source_duration, desired_segment_sec)
                     for source_duration in source_durations])
    if count > 1:
        crossfade = min(crossfade, min(segment_secs) * 0.45)
        desired_segment_sec = (duration_sec + crossfade * (count - 1)) / count
        segment_secs = [min(source_duration, desired_segment_sec)
                        for source_duration in source_durations]
    normalized: list[Path] = []
    inputs_report: list[dict] = []
    for i, (source, segment_sec, source_duration) in enumerate(
            zip(asset_paths, segment_secs, source_durations)):
        normalize_source = Path(source)
        if source_start_sec > 0:
            normalize_source = work / f"track-{i + 1:02d}-source-trimmed.wav"
            trim_duration = min(segment_sec, source_duration)
            _run(["ffmpeg", "-y", "-ss", f"{source_start_sec:.6f}", "-i", str(source),
                  "-t", f"{trim_duration:.6f}", "-af",
                  "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo",
                  "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(normalize_source)])
        target = work / f"track-{i + 1:02d}-normalized.wav"
        _, measured = normalize_track(normalize_source, target, target_lufs=BGM_REFERENCE_LUFS,
                                      duration_sec=segment_sec)
        normalized.append(target)
        inputs_report.append({"path": str(source), "segmentSec": round(segment_sec, 3),
                              "sourceStartSec": round(source_start_sec, 3), **measured})

    joined = normalized[0]
    sequence_indices = [0]
    transition_times: list[float] = []
    if count > 1:
        # 한 바퀴가 영상보다 짧으면 A,B,C,A,B,C 순으로 확장한다. 각 경계는
        # acrossfade로 연결하므로 cycle 끝에서 첫 곡으로 돌아갈 때도 하드컷이 없다.
        joined_duration = segment_secs[0]
        next_index = 1
        while joined_duration < duration_sec - 1e-6:
            increment = segment_secs[next_index] - crossfade
            if increment <= 0.01:
                raise AudioMixError("playlist 곡 길이가 crossfade보다 짧아 반복할 수 없음")
            transition_times.append(joined_duration - crossfade / 2.0)
            sequence_indices.append(next_index)
            joined_duration += increment
            next_index = (next_index + 1) % count
        joined = work / "playlist-joined.wav"
        cmd = ["ffmpeg", "-y"]
        for index in sequence_indices:
            cmd += ["-i", str(normalized[index])]
        chain: list[str] = []
        previous = "[0:a]"
        for i in range(1, len(sequence_indices)):
            label = f"[xf{i}]"
            chain.append(
                f"{previous}[{i}:a]acrossfade=d={crossfade:.6f}:c1=qsin:c2=qsin{label}"
            )
            previous = label
        cmd += ["-filter_complex", ";".join(chain), "-map", previous,
                "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(joined)]
        _run(cmd)

    out = finish_bgm(joined, out_path, duration_sec=duration_sec, gain_db=gain_db,
                     fade_in_sec=fade_in_sec, fade_out_sec=fade_out_sec)
    final = measure_loudness(out, duration_sec=duration_sec)
    return out, {"kind": "playlist" if count > 1 else "asset", "tracks": inputs_report,
                 "crossfadeSec": round(crossfade, 3), "gainDb": gain_db,
                 "sequenceAssetIndexes": sequence_indices,
                 "transitionTimesSec": [round(t, 3) for t in transition_times
                                        if 0 < t < duration_sec],
                 "cycleDurationSec": round(sum(segment_secs) - crossfade * (count - 1), 3),
                 "output": final}


def normalize_voice(input_path: str | Path, out_path: str | Path, *, duration_sec: float) -> tuple[Path, dict]:
    """음성은 loop하지 않고 끝을 무음으로 pad한 뒤 -16 LUFS 기준으로 정규화한다."""
    source_duration = probe_duration(input_path)
    analyze_duration = min(duration_sec, source_duration)
    measured = measure_loudness(input_path, duration_sec=analyze_duration)
    filt = (
        f"loudnorm=I={VOICE_TARGET_LUFS}:LRA=11:TP={TRUE_PEAK_DB}:"
        f"measured_I={measured['integratedLufs']}:measured_LRA={measured['lra']}:"
        f"measured_TP={measured['truePeakDbtp']}:measured_thresh={measured['threshold']}:"
        f"offset={measured['targetOffset']}:linear=true,apad,atrim=duration={duration_sec:.6f},"
        "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo"
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-i", str(input_path), "-af", filt, "-ar", str(SR), "-ac", "2",
          "-c:a", "pcm_s16le", str(out)])
    return out, measured


def _envelope_100hz(path: str | Path, duration_sec: float) -> list[float]:
    """긴 오디오도 작게 유지되는 100Hz mono 절대진폭 envelope를 반환한다."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path), "-t", f"{duration_sec:.6f}",
        "-af", "pan=mono|c0=0.5*c0+0.5*c1,aeval=abs(val(0)),aresample=100",
        "-ac", "1", "-ar", "100", "-f", "f32le", "pipe:1",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise AudioMixError(f"ducking envelope 측정 실패: {path}\n{exc.stderr[-2000:]!r}") from exc
    values = array("f")
    values.frombytes(res.stdout)
    return list(values)


def _rms_db(values: list[float]) -> float:
    if not values:
        return -120.0
    return 20.0 * math.log10(max(1e-9, math.sqrt(sum(v * v for v in values) / len(values))))


def measure_ducking_regions(voice_path: str | Path, bgm_before_path: str | Path,
                            bgm_after_path: str | Path, *, duration_sec: float) -> dict | None:
    """음성 활성/비활성 구간에서 BGM 감쇄와 복귀를 실제 stem 파형으로 측정한다."""
    voice = _envelope_100hz(voice_path, duration_sec)
    before = _envelope_100hz(bgm_before_path, duration_sec)
    after = _envelope_100hz(bgm_after_path, duration_sec)
    count = min(len(voice), len(before), len(after))
    if count < 20:
        return None
    voice, before, after = voice[:count], before[:count], after[:count]
    peak = max(voice)
    if peak <= 1e-6:
        return None
    active_threshold = max(0.001, peak * 0.08)
    inactive_threshold = active_threshold * 0.25
    active = [i for i, value in enumerate(voice) if value >= active_threshold]
    inactive = [i for i, value in enumerate(voice) if value <= inactive_threshold]
    # 100Hz 기준 최소 0.2초 이상의 표본이 양쪽에 있어야 구간 비교가 의미 있다.
    if len(active) < 20 or len(inactive) < 20:
        return None

    def attenuation(indices: list[int]) -> tuple[float, float, float]:
        before_db = _rms_db([before[i] for i in indices])
        after_db = _rms_db([after[i] for i in indices])
        return before_db, after_db, max(0.0, before_db - after_db)

    active_before, active_after, active_attenuation = attenuation(active)
    inactive_before, inactive_after, inactive_attenuation = attenuation(inactive)
    return {
        "rateHz": 100,
        "activeCoverage": round(len(active) / count, 4),
        "inactiveCoverage": round(len(inactive) / count, 4),
        "activeBeforeRmsDb": round(active_before, 2),
        "activeAfterRmsDb": round(active_after, 2),
        "activeAttenuationDb": round(active_attenuation, 2),
        "inactiveBeforeRmsDb": round(inactive_before, 2),
        "inactiveAfterRmsDb": round(inactive_after, 2),
        "inactiveAttenuationDb": round(inactive_attenuation, 2),
    }


def mix_voice_and_bgm(voice_path: str | Path, bgm_path: str | Path, out_path: str | Path,
                      *, duration_sec: float, ducking_enabled: bool,
                      ducking_amount_db: float, attack_ms: int, release_ms: int,
                      work_dir: str | Path) -> tuple[Path, dict]:
    work = Path(work_dir)
    voice_norm, voice_before = normalize_voice(voice_path, work / "narration-normalized.wav",
                                               duration_sec=duration_sec)
    # sidechaincompress는 amountDb를 직접 받지 않으므로 감쇄 의도를 ratio로 보수 매핑한다.
    ratio = min(20.0, max(1.0, 1.0 + ducking_amount_db * 0.9))
    # wet/dry mix의 dry floor가 요청 감쇄량보다 낮아지지 않도록 제한한다.
    # compressor가 완전히 눌려도 dry=(1-wet)이 남아 최대 감쇄가 amountDb를 넘지 않는다.
    wet_mix = min(1.0, max(0.0, 1.0 - 10 ** (-ducking_amount_db / 20.0)))
    bgm_before = measure_loudness(bgm_path, duration_sec=duration_sec)
    ducked_bgm = work / "bgm-ducked.wav"
    if ducking_enabled and ducking_amount_db > 0:
        duck_filter = (
            f"[0:a][1:a]sidechaincompress=threshold=0.025:ratio={ratio:.3f}:"
            f"attack={attack_ms}:release={release_ms}:makeup=1:mix={wet_mix:.6f},"
            "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[out]"
        )
        _run(["ffmpeg", "-y", "-i", str(bgm_path), "-i", str(voice_norm),
              "-filter_complex", duck_filter, "-map", "[out]", "-t", f"{duration_sec:.6f}",
              "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(ducked_bgm)])
    else:
        copy_master(bgm_path, ducked_bgm)
    bgm_after = measure_loudness(ducked_bgm, duration_sec=duration_sec)
    measured_attenuation = max(
        0.0, bgm_before["integratedLufs"] - bgm_after["integratedLufs"]
    )
    region_metrics = measure_ducking_regions(
        voice_norm, bgm_path, ducked_bgm, duration_sec=duration_sec
    ) if ducking_enabled and ducking_amount_db > 0 else None
    # FFmpeg 버전에 따라 단일 pass loudnorm는 -16 LUFS 목표에서 1dB 이상 흔들릴 수 있다.
    # 먼저 믹스하고, 고정 길이의 2-pass loudnorm으로 master를 확정한다.
    filters = (
        "[0:a][1:a]amix=inputs=2:duration=longest:dropout_transition=0,"
        "alimiter=limit=" + f"{LIMIT_LINEAR:.8f}:level=false,"
        "aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[mix]"
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    mixed = work / "master-pre-normalized.wav"
    _run(["ffmpeg", "-y", "-i", str(ducked_bgm), "-i", str(voice_norm),
          "-filter_complex", filters, "-map", "[mix]", "-t", f"{duration_sec:.6f}",
          "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(mixed)])
    normalize_track(mixed, out, target_lufs=VOICE_TARGET_LUFS, duration_sec=duration_sec)
    final = measure_loudness(out, duration_sec=duration_sec)
    return out, {"voiceInput": voice_before, "ducking": {"enabled": ducking_enabled,
                  "requestedAmountDb": ducking_amount_db, "ratio": ratio,
                  "wetMix": round(wet_mix, 6),
                  "attackMs": attack_ms, "releaseMs": release_ms,
                  "bgmBefore": bgm_before, "bgmAfter": bgm_after,
                  "measuredAttenuationDb": round(measured_attenuation, 2),
                  "regionMetrics": region_metrics,
                  "stemPath": str(ducked_bgm)}, "output": final}


def copy_master(input_path: str | Path, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, out)
    return out


def write_mix_report(out_path: str | Path, report: dict) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schemaVersion": 1,
               "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"), **report}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out

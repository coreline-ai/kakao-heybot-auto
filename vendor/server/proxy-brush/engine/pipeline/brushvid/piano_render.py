"""Local SFZ/WAV sampled piano renderer for the piano-bgm skill."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .piano_bgm import PianoBgmError

OUTPUT_SR = 48_000
_SAMPLE_RE = re.compile(r"(\w+)=([^\s]+)")


@dataclass(frozen=True)
class Region:
    key: int
    low_velocity: int
    high_velocity: int
    keycenter: int
    tune_cents: float
    volume_db: float
    sample: Path


def parse_sfz(path: Path, sample_dir: Path) -> list[Region]:
    regions: list[Region] = []
    group_tune = 0.0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PianoBgmError(f"SFZ 로드 실패: {path}") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("<group>"):
            fields = dict(_SAMPLE_RE.findall(line))
            group_tune = float(fields.get("tune", "0"))
            continue
        if not line.startswith("<region>"):
            continue
        fields = dict(_SAMPLE_RE.findall(line))
        required = ("sample", "lokey", "hikey", "lovel", "pitch_keycenter")
        if any(field not in fields for field in required) or fields["lokey"] != fields["hikey"]:
            continue
        sample = sample_dir / Path(fields["sample"].replace("\\", "/")).name
        regions.append(Region(key=int(fields["lokey"]), low_velocity=int(fields["lovel"]),
                              high_velocity=int(fields.get("hivel", "127")), keycenter=int(fields["pitch_keycenter"]),
                              tune_cents=group_tune, volume_db=float(fields.get("volume", "0")), sample=sample))
    if not regions:
        raise PianoBgmError(f"연주 가능한 SFZ region 없음: {path}")
    return regions


def select_region(regions: list[Region], midi: int, velocity: int) -> Region:
    choices = [region for region in regions if region.key == midi and region.low_velocity <= velocity <= region.high_velocity]
    if len(choices) != 1:
        raise PianoBgmError(f"sample region 선택 실패 midi={midi}, velocity={velocity}, matches={len(choices)}")
    if not choices[0].sample.is_file():
        raise PianoBgmError(f"sample WAV 없음: {choices[0].sample}")
    return choices[0]


def _resample(audio: np.ndarray, ratio: float) -> np.ndarray:
    fraction = Fraction(1.0 / ratio).limit_denominator(8192)
    return resample_poly(audio, fraction.numerator, fraction.denominator, axis=0).astype(np.float32)


@lru_cache(maxsize=128)
def _load_source(sample_path: str) -> np.ndarray:
    audio, source_rate = sf.read(sample_path, dtype="float32", always_2d=True)
    if source_rate != OUTPUT_SR:
        ratio = Fraction(OUTPUT_SR, source_rate).limit_denominator(8192)
        audio = resample_poly(audio, ratio.numerator, ratio.denominator, axis=0).astype(np.float32)
    return audio


def _note_audio(region: Region, midi: int, hold_sec: float, release_sec: float) -> np.ndarray:
    audio = _load_source(str(region.sample)).copy()
    speed = 2 ** ((midi - region.keycenter + region.tune_cents / 100.0) / 12.0)
    audio = _resample(audio, speed)
    tail_sec = min(4.5, max(1.0, release_sec + .8))
    keep = min(len(audio), int(round((hold_sec + tail_sec) * OUTPUT_SR)))
    audio = audio[:keep].copy()
    release_start = min(len(audio), int(round(hold_sec * OUTPUT_SR)))
    if release_start < len(audio):
        length = min(len(audio) - release_start, int(round(release_sec * OUTPUT_SR)))
        if length:
            fade = np.cos(np.linspace(0, math.pi / 2, length, endpoint=True)) ** .72
            audio[release_start:release_start + length] *= fade[:, None]
        if release_start + length < len(audio):
            audio[release_start + length:] = 0
    return audio * (10 ** (region.volume_db / 20.0))


def render_performance(performance: dict[str, Any], regions: list[Region]) -> tuple[np.ndarray, dict[str, Any]]:
    duration = float(performance["durationSec"])
    buffer = np.zeros((int(round(duration * OUTPUT_SR)), 2), dtype=np.float32)
    selected: list[dict[str, Any]] = []
    for index, event in enumerate(performance["notes"]):
        start, hold, midi, velocity = float(event["startSec"]), float(event["holdSec"]), int(event["midi"]), int(event["velocity"])
        if not (0 <= start < duration and hold > 0 and 21 <= midi <= 108 and 1 <= velocity <= 127):
            raise PianoBgmError(f"performance event 범위 오류 index={index}: {event}")
        region = select_region(regions, midi, velocity)
        tone = _note_audio(region, midi, hold, float(event.get("releaseSec", 1.25))) * float(event.get("gain", 1.0))
        offset = int(round(start * OUTPUT_SR))
        length = min(len(tone), len(buffer) - offset)
        buffer[offset:offset + length] += tone[:length]
        selected.append({"midi": midi, "velocity": velocity, "sample": region.sample.name, "part": event.get("part")})
    peak = float(np.max(np.abs(buffer))) if len(buffer) else 0.0
    if peak <= 0:
        raise PianoBgmError("무음 render 결과")
    if peak > .90:
        buffer *= .90 / peak
    return buffer, {"sampleRateHz": OUTPUT_SR, "channels": 2, "regions": len(regions), "noteCount": len(performance["notes"]),
                    "peakBeforeGuard": peak, "peakAfterGuard": float(np.max(np.abs(buffer))),
                    "uniqueSampleCount": len({item["sample"] for item in selected}), "samplePreview": selected[:16]}


def render_to_wav(performance: dict[str, Any], *, instrument_root: Path, sfz: Path, output: Path) -> dict[str, Any]:
    sample_dir = instrument_root / "48khz24bit"
    regions = parse_sfz(sfz, sample_dir)
    audio, report = render_performance(performance, regions)
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, audio, OUTPUT_SR, subtype="PCM_24")
    report["out"] = str(output)
    return report


def write_render_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

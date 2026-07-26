"""Original, harmony-aware solo piano BGM composition and QA.

The module creates symbolic scores only from a request and deterministic seed.  It
never reads a finished composition, MIDI loop, online audio, or external sample
beyond the separately licensed instrument rendered by :mod:`piano_render`.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUEST_SCHEMA_PATH = REPO_ROOT / "schema" / "piano-bgm-request.schema.json"
INSTRUMENT_CATALOG_PATH = REPO_ROOT / "assets" / "instruments" / "catalog.json"
PROJECTS_ROOT = REPO_ROOT / "projects" / "piano-bgm"
OUTPUT_ROOT = REPO_ROOT / "output" / "original-audio" / "piano-bgm"

NOTE_PCS = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4,
            "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
            "A#": 10, "BB": 10, "B": 11}
MODE_INTERVALS = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
}
CHORD_INTERVALS = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "maj7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "7": (0, 4, 7, 10), "sus2": (0, 2, 7),
    "open5": (0, 7), "add9": (0, 4, 7, 14), "dim": (0, 3, 6),
}


class PianoBgmError(ValueError):
    """Invalid request, score, render input, or delivery state."""


@dataclass(frozen=True)
class Key:
    tonic_pc: int
    mode: str

    @property
    def pitch_classes(self) -> tuple[int, ...]:
        return tuple((self.tonic_pc + interval) % 12 for interval in MODE_INTERVALS[self.mode])

    @property
    def label(self) -> str:
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        return f"{names[self.tonic_pc]} {self.mode}"

    @classmethod
    def parse(cls, value: str) -> "Key":
        match = re.fullmatch(r"\s*([A-Ga-g])([#bB]?)[-_ ](major|minor|lydian)\s*", value or "")
        if not match:
            raise PianoBgmError("key는 예: G-major, A-minor, D-lydian 형식이어야 함")
        note = (match.group(1).upper() + match.group(2).upper()).replace("B", "B")
        if note not in NOTE_PCS:
            raise PianoBgmError(f"지원하지 않는 key tonic: {match.group(1)}{match.group(2)}")
        return cls(NOTE_PCS[note], match.group(3))


@dataclass(frozen=True)
class Meter:
    signature: str
    tempo_unit: str
    meter_beats: int

    @property
    def slots(self) -> int:
        return 6 if self.signature == "6/8" else self.meter_beats * 2

    @property
    def strong_slots(self) -> tuple[int, ...]:
        if self.signature == "6/8":
            return (0, 3)
        if self.signature == "3/4":
            return (0,)
        return (0, self.slots // 2)

    @property
    def beat_quarters(self) -> float:
        return 3.0 if self.signature == "6/8" else float(self.meter_beats)

    @property
    def tempo_beats_per_bar(self) -> int:
        return 2 if self.tempo_unit == "dotted-quarter" else self.meter_beats


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    key: str
    tempo_bpm: int
    meter: Meter
    progression: tuple[tuple[int, str], ...]
    style: str
    density: str
    allow_unresolved_tension: bool = False


PRESETS: dict[str, Preset] = {
    "new-age": Preset("new-age", "뉴에이지 피아노", "G-major", 68, Meter("4/4", "quarter-note", 4),
                       ((1, "maj7"), (5, "sus2"), (6, "m7"), (4, "maj7")), "flowing", "medium"),
    "ambient-piano": Preset("ambient-piano", "앰비언트 피아노", "D-minor", 48, Meter("4/4", "quarter-note", 4),
                              ((1, "open5"), (6, "open5"), (3, "add9"), (7, "open5")), "spacious", "low"),
    "sleep-piano": Preset("sleep-piano", "수면 피아노", "C-major", 60, Meter("3/4", "quarter-note", 3),
                           ((1, "maj7"), (6, "m7"), (4, "maj7"), (5, "7")), "lullaby", "medium"),
    "meditation-piano": Preset("meditation-piano", "명상 피아노", "D-minor", 40, Meter("4/4", "quarter-note", 4),
                                ((1, "open5"), (4, "open5"), (6, "open5"), (5, "open5")), "breath", "low"),
    "lofi-piano": Preset("lofi-piano", "로파이 피아노", "A-minor", 80, Meter("4/4", "quarter-note", 4),
                          ((1, "m7"), (4, "m7"), (7, "7"), (3, "maj7")), "lofi", "high"),
    "minimal-ambient": Preset("minimal-ambient", "미니멀 앰비언트 피아노", "C-major", 60, Meter("6/8", "dotted-quarter", 3),
                               ((1, "open5"), (6, "open5"), (4, "open5"), (5, "open5")), "minimal", "low"),
    "cinematic-piano": Preset("cinematic-piano", "시네마틱 피아노", "C-minor", 56, Meter("4/4", "quarter-note", 4),
                               ((1, "minor"), (6, "maj7"), (4, "m7"), (5, "minor")), "cinematic", "high"),
    "film-ost-piano": Preset("film-ost-piano", "영화 OST풍 피아노", "D-major", 60, Meter("3/4", "quarter-note", 3),
                              ((1, "maj7"), (5, "major"), (6, "m7"), (4, "maj7")), "ost", "medium"),
    "game-bgm-piano": Preset("game-bgm-piano", "게임 BGM풍 피아노", "E-minor", 60, Meter("6/8", "dotted-quarter", 3),
                              ((1, "m7"), (3, "maj7"), (6, "maj7"), (7, "7")), "game", "high"),
    "fantasy-piano": Preset("fantasy-piano", "판타지 피아노", "D-lydian", 72, Meter("3/4", "quarter-note", 3),
                             ((1, "maj7"), (2, "major"), (5, "major"), (7, "m7")), "fantasy", "medium"),
    "mystery-horror-piano": Preset("mystery-horror-piano", "미스터리·호러 피아노", "C#-minor", 48, Meter("4/4", "quarter-note", 4),
                                    ((1, "minor"), (4, "minor"), (7, "major"), (5, "minor")), "horror", "low", True),
}

ENGINE_IDS = ("auto", "stable-audio-3-mlx", "sample-score")
STABLE_AUDIO_PRESETS = frozenset({
    "cinematic-piano", "film-ost-piano", "game-bgm-piano", "fantasy-piano", "mystery-horror-piano",
})


# A 방식: melody-led bedtime routines. These select existing acoustic-piano presets;
# they never add a sine drone, binaural beat, pink-noise pulse, or other frequency layer.
SLEEP_ROUTINES: dict[str, dict[str, Any]] = {
    "lullaby": {"title": "포근한 자장가", "preset": "sleep-piano", "key": "C-major", "tempoBpm": 60,
                 "mood": "soft-safe"},
    "breath": {"title": "느린 호흡", "preset": "minimal-ambient", "key": "C-major", "tempoBpm": 56,
               "mood": "weightless-gentle"},
    "window": {"title": "고요한 밤창", "preset": "ambient-piano", "key": "D-minor", "tempoBpm": 48,
               "mood": "quiet-night"},
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _load_schema(path: Path = REQUEST_SCHEMA_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PianoBgmError(f"piano BGM request schema 로드 실패: {path}") from exc


def _schema_errors(request: dict[str, Any]) -> list[str]:
    schema = _load_schema()
    errors = sorted(Draft202012Validator(schema).iter_errors(request), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]


def normalize_request(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise PianoBgmError("request는 YAML/JSON 객체여야 함")
    errors = _schema_errors(request)
    if errors:
        raise PianoBgmError("request schema 위반: " + "; ".join(errors[:5]))
    sleep_routine = request.get("sleepRoutine", "none")
    routine = SLEEP_ROUTINES.get(sleep_routine)
    if sleep_routine != "none" and routine is None:
        raise PianoBgmError(f"지원하지 않는 sleepRoutine: {sleep_routine}")
    if routine and request.get("preset") and request["preset"] != routine["preset"]:
        raise PianoBgmError(f"sleepRoutine={sleep_routine}은 preset={routine['preset']}만 사용합니다")
    preset_id = routine["preset"] if routine else request.get("preset")
    if not preset_id:
        raise PianoBgmError("일반 BGM에는 preset이 필요합니다. 수면 루틴은 sleepRoutine만 지정할 수 있습니다")
    preset = PRESETS[preset_id]
    try:
        key = Key.parse(request.get("key") or (routine["key"] if routine else preset.key))
    except PianoBgmError:
        raise
    duration = float(request["durationSec"])
    if not 15.0 <= duration <= 600.0:
        raise PianoBgmError("durationSec는 15~600초여야 함")
    engine = request.get("engine", "sample-score")
    if engine not in ENGINE_IDS:
        raise PianoBgmError(f"engine은 {ENGINE_IDS} 중 하나여야 함")
    if engine == "stable-audio-3-mlx" and duration > 120.0:
        raise PianoBgmError("stable-audio-3-mlx의 sm-music durationSec는 15~120초여야 함")
    tempo = int(request.get("tempoBpm") or (routine["tempoBpm"] if routine else preset.tempo_bpm))
    if not 32 <= tempo <= 160:
        raise PianoBgmError("tempoBpm는 32~160이어야 함")
    seed = int(request.get("seed") if request.get("seed") is not None else int(hashlib.sha256(request["projectId"].encode()).hexdigest()[:8], 16))
    output = dict(request.get("output") or {})
    output.setdefault("distribution", "local")
    output.setdefault("preview", True)
    normalized = {
        "schemaVersion": 1,
        "projectId": request["projectId"], "kind": "piano-bgm", "durationSec": duration,
        "preset": preset.id, "sleepRoutine": sleep_routine,
        "mood": request.get("mood") or (routine["mood"] if routine else "balanced"),
        "purpose": request.get("purpose", "background"),
        "engine": engine,
        "cfg": float(request.get("cfg", 1.0)),
        "steps": int(request.get("steps", 8)),
        "key": f"{key.label.split()[0]}-{key.mode}", "tempoBpm": tempo, "seed": seed, "output": output,
    }
    # Optional schema fields stay omitted when absent. Keeping them as explicit
    # None values would make a normalized request fail schema validation when
    # compose/load paths normalize it again.
    if request.get("prompt") is not None:
        normalized["prompt"] = request["prompt"]
    if request.get("negativePrompt") is not None:
        normalized["negativePrompt"] = request["negativePrompt"]
    return normalized


def resolve_engine(request: dict[str, Any]) -> dict[str, str]:
    """Resolve an explicit or auto engine without checking local runtime availability."""
    engine = request.get("engine", "sample-score")
    if engine != "auto":
        return {"engine": engine, "reason": "explicit"}
    if request["durationSec"] > 120:
        return {"engine": "sample-score", "reason": "stable-audio-sm-music-max-120s"}
    if request.get("purpose") == "featured" or request.get("preset") in STABLE_AUDIO_PRESETS:
        return {"engine": "stable-audio-3-mlx", "reason": "featured-or-cinematic-preset"}
    return {"engine": "sample-score", "reason": "sleep-or-deterministic-score"}


def load_request(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PianoBgmError(f"request 로드 실패: {source}: {exc}") from exc
    return normalize_request(data)


def write_request(path: str | Path, request: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(request, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return target


def _midi_for_pc(pc: int, target: int, *, lower: int = 21, upper: int = 108) -> int:
    candidates = [midi for midi in range(lower, upper + 1) if midi % 12 == pc]
    return min(candidates, key=lambda midi: (abs(midi - target), midi))


def _chord_pcs(root_pc: int, quality: str) -> tuple[int, ...]:
    try:
        intervals = CHORD_INTERVALS[quality]
    except KeyError as exc:
        raise PianoBgmError(f"unknown chord quality: {quality}") from exc
    return tuple((root_pc + interval) % 12 for interval in intervals)


def _chord_for_degree(key: Key, degree: int, quality: str) -> dict[str, Any]:
    root = key.pitch_classes[(degree - 1) % len(key.pitch_classes)]
    return {"degree": degree, "rootPc": root, "quality": quality, "pitchClasses": list(_chord_pcs(root, quality))}


def _bar_timing(request: dict[str, Any], preset: Preset) -> tuple[int, float, float]:
    nominal_bar = preset.meter.tempo_beats_per_bar * 60.0 / request["tempoBpm"]
    bars = max(1, round(request["durationSec"] / nominal_bar))
    bar_sec = request["durationSec"] / bars
    resolved_tempo = 60.0 * preset.meter.tempo_beats_per_bar / bar_sec
    return bars, bar_sec, resolved_tempo


def _nearest_ascending(pcs: Iterable[int], target: int, *, minimum_gap: int = 3) -> list[int]:
    result: list[int] = []
    for index, pc in enumerate(pcs):
        candidate = _midi_for_pc(pc, target + index * minimum_gap)
        while result and candidate <= result[-1]:
            candidate += 12
        result.append(candidate)
    return result


def _voicing(chord: dict[str, Any], previous: list[int] | None) -> list[int]:
    pcs = chord["pitchClasses"]
    inner = pcs[1:] if len(pcs) > 2 else pcs
    candidates: list[list[int]] = []
    for inversion in range(len(inner)):
        rotated = inner[inversion:] + inner[:inversion]
        candidates.append(_nearest_ascending(rotated, 49))
    if previous is None:
        return min(candidates, key=lambda values: sum(abs(value - 52) for value in values))
    return min(candidates, key=lambda values: sum(abs(a - b) for a, b in zip(values, previous)))


def _melody_pitch(chord: dict[str, Any], previous: int | None, *, target: int, slot: int, high: bool = False) -> int:
    pcs = chord["pitchClasses"]
    # Third/fifth/seventh create a melodic contour without leaving the active chord on strong attacks.
    pc = pcs[(slot + (1 if len(pcs) > 2 else 0)) % len(pcs)]
    desired = target + (5 if high else 0)
    candidates = [_midi_for_pc(pc, desired + octave) for octave in (-12, 0, 12)]
    # Right hand never drops into the accompaniment register just to shorten a leap.
    candidates = [value for value in candidates if value >= desired - 6] or candidates
    if previous is None:
        return min(candidates, key=lambda value: abs(value - desired))
    return min(candidates, key=lambda value: (abs(value - previous), abs(value - desired)))


def _event(start: float, duration: float, midi: int, *, hand: str, role: str, chord_id: int,
           strength: str, tension: bool = False, resolves_to: int | None = None) -> dict[str, Any]:
    return {"startSec": round(start, 6), "durationSec": round(max(0.045, duration), 6), "midi": int(midi),
            "hand": hand, "role": role, "chordId": chord_id, "strength": strength,
            "isTension": tension, "resolvesTo": resolves_to}


def _add(events: list[dict[str, Any]], *args: Any, **kwargs: Any) -> None:
    events.append(_event(*args, **kwargs))


def _strength(meter: Meter, slot: int) -> str:
    return "strong" if slot in meter.strong_slots else "weak"


def _style_pattern(preset: Preset, meter: Meter) -> tuple[list[int], list[int], list[int]]:
    """Returns left slots, melody slots, and chord slot count in one bar."""
    slots = meter.slots
    if preset.style == "spacious":
        return ([0, slots // 2], [slots // 2], slots)
    if preset.style == "breath":
        return ([0], [slots // 2], slots)
    if preset.style == "lullaby":
        return ([0, 2, 4], [0, 2, 4], slots)
    if preset.style == "lofi":
        return ([0, 5], [1, 3, 5, 7], slots)
    if preset.style == "minimal":
        return ([0, 3], [1, 4], slots)
    if preset.style == "game":
        return (list(range(slots)), [1, 3, 5], slots)
    if preset.style == "fantasy":
        return ([0, 2, 4], [0, 1, 2, 3, 4, 5], slots)
    if preset.style == "horror":
        return ([0], [3], slots)
    if preset.style == "cinematic":
        return (list(range(0, slots, 2)), [1, 3, 5, 7], slots)
    if preset.style == "ost":
        return ([0, 2, 4], [0, 2, 4], slots)
    return (list(range(slots)), [1, 3, 5, 7], slots)


def compose(request: dict[str, Any]) -> dict[str, Any]:
    request = normalize_request(request)
    preset, key = PRESETS[request["preset"]], Key.parse(request["key"])
    bars, bar_sec, resolved_tempo = _bar_timing(request, preset)
    meter = preset.meter
    harmony: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    previous_voicing: list[int] | None = None
    previous_melody: int | None = None
    left_slots, melody_slots, slots = _style_pattern(preset, meter)
    for bar in range(bars):
        degree, quality = preset.progression[bar % len(preset.progression)]
        if bar == bars - 1:
            degree, quality = 1, ("minor" if key.mode == "minor" else "major")
        chord = _chord_for_degree(key, degree, quality)
        chord["bar"] = bar
        harmony.append(chord)
        base = bar * bar_sec
        slot_sec = bar_sec / slots
        root_bass = _midi_for_pc(chord["rootPc"], 38)
        voicing = _voicing(chord, previous_voicing)
        previous_voicing = voicing
        # Bass and lower chord voice share the exact harmony plan with the melody.
        for index, slot in enumerate(left_slots):
            start = base + slot * slot_sec
            hold = slot_sec * (1.65 if preset.style in ("flowing", "game", "cinematic") else 1.95)
            if index == 0:
                _add(events, start, hold, root_bass, hand="left", role="bass", chord_id=bar,
                     strength=_strength(meter, slot))
            else:
                tone = voicing[index % len(voicing)]
                _add(events, start, hold, tone, hand="left", role="arpeggio", chord_id=bar,
                     strength=_strength(meter, slot))
        if preset.style in ("spacious", "breath", "minimal"):
            # Sustained inner voices instead of a busy accompaniment.
            start = base + slot_sec * (1 if preset.style == "minimal" else 0.5)
            for tone in voicing[: min(2, len(voicing))]:
                _add(events, start, bar_sec * 0.66, tone, hand="left", role="voicing", chord_id=bar, strength="weak")
        if preset.style == "lofi":
            for slot in (2, 6):
                for tone in voicing:
                    _add(events, base + slot * slot_sec, slot_sec * 0.7, tone + 12, hand="left", role="comp", chord_id=bar,
                         strength=_strength(meter, slot))
        if preset.style == "horror":
            # This is declared, controlled tension rather than a stray high note.
            tension_pc = (chord["rootPc"] + 6) % 12
            _add(events, base + 3 * slot_sec, slot_sec * 0.72, _midi_for_pc(tension_pc, 65), hand="right",
                 role="declared-tension", chord_id=bar, strength="weak", tension=True)
        high = preset.style in ("fantasy", "cinematic")
        for index, slot in enumerate(melody_slots):
            if preset.style == "spacious" and bar % 2:
                continue
            start = base + slot * slot_sec + (0.012 if preset.style == "lofi" and slot % 2 else 0.0)
            tone = _melody_pitch(chord, previous_melody, target=77 if high else 72, slot=index + bar, high=high)
            previous_melody = tone
            hold = slot_sec * (1.15 if index == len(melody_slots) - 1 else 0.82)
            _add(events, start, hold, tone, hand="right", role="melody", chord_id=bar,
                 strength=_strength(meter, slot))
        # A final cadence strengthens arrival without adding an unrelated top note.
        if bar == bars - 1:
            top = _midi_for_pc(chord["rootPc"], 79 if high else 74)
            _add(events, base + bar_sec * 0.67, bar_sec * 0.3, top, hand="right", role="cadence", chord_id=bar, strength="weak")
    score = {
        "schemaVersion": 1,
        "projectId": request["projectId"], "durationSec": request["durationSec"], "preset": preset.id,
        "presetTitle": preset.title, "sleepRoutine": request.get("sleepRoutine", "none"),
        "purpose": request["purpose"], "mood": request["mood"], "seed": request["seed"],
        "key": {"tonicPc": key.tonic_pc, "mode": key.mode, "label": key.label, "pitchClasses": list(key.pitch_classes)},
        "meter": asdict(meter), "requestedTempoBpm": request["tempoBpm"], "resolvedTempoBpm": round(resolved_tempo, 6),
        "bars": bars, "barDurationSec": round(bar_sec, 9), "harmony": harmony,
        "notes": sorted(events, key=lambda e: (e["startSec"], e["midi"], e["hand"])),
        "generation": {"engine": "piano-bgm", "version": "0.1.0", "source": "original deterministic score events only"},
    }
    score["scoreSha256"] = sha256_json({key: value for key, value in score.items() if key != "scoreSha256"})
    return score


def lint_score(score: dict[str, Any]) -> dict[str, Any]:
    """Reject out-of-key notes and non-resolving/strong-beat collisions before render."""
    issues: list[dict[str, Any]] = []
    key = Key(int(score["key"]["tonicPc"]), score["key"]["mode"])
    scale = set(key.pitch_classes)
    chords = {int(item["bar"]): item for item in score["harmony"]}
    notes = list(score["notes"])
    duration = float(score["durationSec"])
    preset = PRESETS[score["preset"]]
    melody_by_hand = [note for note in notes if note["hand"] == "right"]
    for index, note in enumerate(notes):
        start, hold, midi = float(note["startSec"]), float(note["durationSec"]), int(note["midi"])
        if not 21 <= midi <= 108 or start < 0 or hold <= 0 or start >= duration:
            issues.append({"code": "event-range", "index": index, "startSec": start})
            continue
        pc = midi % 12
        chord = chords.get(int(note["chordId"]))
        if chord is None:
            issues.append({"code": "missing-chord", "index": index})
            continue
        chord_pcs = set(chord["pitchClasses"])
        if pc not in scale and not note.get("isTension"):
            issues.append({"code": "scale-mismatch", "index": index, "midi": midi, "key": key.label})
        if note["role"] in ("melody", "cadence") and note["strength"] == "strong" and pc not in chord_pcs:
            issues.append({"code": "strong-beat-non-chord", "index": index, "midi": midi, "chord": chord})
        if note.get("isTension") and not preset.allow_unresolved_tension:
            following = [candidate for candidate in melody_by_hand if candidate["startSec"] > start]
            if not following or int(following[0]["midi"]) % 12 not in chord_pcs:
                issues.append({"code": "unresolved-tension", "index": index, "midi": midi})
    # A close semitone/whole-tone/tritone at the same local register is a bad accidental attack.
    for index, note in enumerate(notes):
        if note["hand"] != "right" or note.get("isTension"):
            continue
        for low_index, low in enumerate(notes):
            if low["hand"] != "left" or low.get("isTension"):
                continue
            active = float(low["startSec"]) <= float(note["startSec"]) < float(low["startSec"]) + float(low["durationSec"])
            distance = int(note["midi"]) - int(low["midi"])
            if active and 0 < distance <= 15 and distance % 12 in (1, 2, 6):
                issues.append({"code": "attack-collision", "index": index, "withIndex": low_index,
                               "interval": distance % 12, "startSec": note["startSec"]})
    # Hands must not cross at identical attacks; wide left/right separation remains allowed.
    for time in sorted({float(note["startSec"]) for note in notes}):
        left = [int(note["midi"]) for note in notes if note["hand"] == "left" and abs(float(note["startSec"]) - time) < 1e-6]
        right = [int(note["midi"]) for note in notes if note["hand"] == "right" and abs(float(note["startSec"]) - time) < 1e-6]
        if left and right and max(left) >= min(right):
            issues.append({"code": "voice-crossing", "startSec": time, "leftMax": max(left), "rightMin": min(right)})
    # Unmotivated octave-plus leaps in melody are a warning, not a render blocker only for cinematic/fantasy.
    ordered = sorted((note for note in notes if note["role"] in ("melody", "cadence")), key=lambda n: n["startSec"])
    for previous, current in zip(ordered, ordered[1:]):
        leap = abs(int(current["midi"]) - int(previous["midi"]))
        if leap > 12 and score["preset"] not in ("cinematic-piano", "fantasy-piano"):
            issues.append({"code": "melody-leap", "from": previous["midi"], "to": current["midi"], "startSec": current["startSec"]})
    errors = [issue for issue in issues if issue["code"] not in ("melody-leap",)]
    return {"schemaVersion": 1, "projectId": score["projectId"], "status": "PASS" if not errors else "FAIL",
            "errors": errors, "warnings": [issue for issue in issues if issue["code"] == "melody-leap"],
            "noteCount": len(notes), "key": key.label, "scoreSha256": score.get("scoreSha256")}


def performance_from_score(score: dict[str, Any]) -> dict[str, Any]:
    """Convert notation to role-aware performance events consumed by the renderer."""
    duration = float(score["durationSec"])
    notes = list(score["notes"])
    role_velocity = {"bass": 34, "arpeggio": 36, "voicing": 31, "comp": 38,
                     "melody": 43, "cadence": 45, "declared-tension": 30}
    role_gain = {"bass": .087, "arpeggio": .070, "voicing": .060, "comp": .057,
                 "melody": .094, "cadence": .088, "declared-tension": .070}
    performed: list[dict[str, Any]] = []
    for event in notes:
        time = float(event["startSec"])
        phrase = 0.84 + 0.13 * math.sin(math.pi * min(1.0, time / max(duration, 0.1)))
        role = event["role"]
        melody_active = any(
            candidate["hand"] == "right" and not candidate.get("isTension")
            and float(candidate["startSec"]) <= time < float(candidate["startSec"]) + float(candidate["durationSec"])
            for candidate in notes
        )
        duck = .82 if event["hand"] == "left" and melody_active else 1.0
        background_melody_scale = .78 if score["purpose"] == "background" and role in ("melody", "cadence") else 1.0
        velocity = int(round(role_velocity[role] * phrase * duck * background_melody_scale))
        hold = min(float(event["durationSec"]) * (1.12 if role in ("bass", "voicing") else .97), duration - time)
        performed.append({"startSec": round(time, 6), "holdSec": round(max(.045, hold), 6), "midi": event["midi"],
                          "velocity": max(18, min(72, velocity)), "gain": round(role_gain[role] * duck * background_melody_scale, 4),
                          "part": role, "releaseSec": 1.8 if role in ("bass", "voicing") else 1.15,
                          "chordId": event["chordId"]})
    performance = {"schemaVersion": 1, "projectId": score["projectId"], "durationSec": duration,
                   "scoreSha256": score["scoreSha256"], "notes": performed,
                   "performancePolicy": {"phraseDynamics": True, "accompanimentDucking": True,
                                         "randomVelocity": False, "pedalAwareRelease": True,
                                         "sleepRoutine": score.get("sleepRoutine", "none"),
                                         "frequencyLayer": "none", "electronicDrone": False,
                                         "binauralBeat": False}}
    performance["performanceSha256"] = sha256_json({key: value for key, value in performance.items() if key != "performanceSha256"})
    return performance


def project_paths(project_id: str, *, projects_root: Path = PROJECTS_ROOT, output_root: Path = OUTPUT_ROOT) -> dict[str, Path]:
    return {"project": projects_root / project_id, "output": output_root / project_id}


def write_score_bundle(request: dict[str, Any], score: dict[str, Any], performance: dict[str, Any], lint: dict[str, Any], *,
                     projects_root: Path = PROJECTS_ROOT) -> dict[str, Path]:
    root = projects_root / request["projectId"]
    root.mkdir(parents=True, exist_ok=True)
    request_path = write_request(root / "request.yaml", request)
    paths = {"request": request_path, "score": root / "score.json", "performance": root / "performance.json", "compositionReport": root / "composition-report.json"}
    paths["score"].write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["performance"].write_text(json.dumps(performance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["compositionReport"].write_text(json.dumps(lint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


def load_score_bundle(project_id: str, *, projects_root: Path = PROJECTS_ROOT) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = projects_root / project_id
    try:
        request = load_request(root / "request.yaml")
        score = json.loads((root / "score.json").read_text(encoding="utf-8"))
        performance = json.loads((root / "performance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PianoBgmError(f"score bundle 로드 실패: {project_id}: {exc}") from exc
    return request, score, performance


def media_tool(name: str) -> str:
    """Resolve ffmpeg/ffprobe even when a non-login shell omits Homebrew PATH."""
    candidates = [shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and Path(candidate).exists():
            return str(Path(candidate))
    raise PianoBgmError(f"{name} 실행 파일을 찾지 못했습니다. ffmpeg를 설치하거나 PATH에 추가하세요.")


def probe_audio(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run([media_tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        stream = next(item for item in payload["streams"] if item["codec_type"] == "audio")
    except (subprocess.CalledProcessError, StopIteration, json.JSONDecodeError) as exc:
        raise PianoBgmError(f"오디오 probe 실패: {path}") from exc
    return {"codec": stream["codec_name"], "sampleRateHz": int(stream["sample_rate"]), "channels": int(stream["channels"]),
            "bitsPerRawSample": int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0),
            "durationSec": float(payload["format"]["duration"])}


def measure_loudness(path: Path, *, target_i: float = -20.0, target_tp: float = -1.5) -> dict[str, float]:
    try:
        result = subprocess.run([media_tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-af",
                                 f"loudnorm=I={target_i}:LRA=7:TP={target_tp}:print_format=json", "-f", "null", "-"],
                                check=True, capture_output=True, text=True)
        matches = re.findall(r"\{\s*\"input_i\".*?\}", result.stderr, re.S)
        data = json.loads(matches[-1])
    except (subprocess.CalledProcessError, IndexError, json.JSONDecodeError) as exc:
        raise PianoBgmError(f"loudness 검사 실패: {path}") from exc
    return {"integratedLufs": float(data["input_i"]), "truePeakDbtp": float(data["input_tp"]), "lra": float(data["input_lra"])}


def _manifest_path(path: Path) -> str:
    """Keep repo-local manifests portable, while custom test/output roots stay valid."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def instrument_record() -> dict[str, Any]:
    try:
        catalog = json.loads(INSTRUMENT_CATALOG_PATH.read_text(encoding="utf-8"))
        return catalog["instruments"][0]
    except (OSError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise PianoBgmError("instrument catalog 로드 실패") from exc


def qa_project(project_id: str, *, projects_root: Path = PROJECTS_ROOT, output_root: Path = OUTPUT_ROOT,
              engine_selection: dict[str, str] | None = None) -> dict[str, Any]:
    request, score, performance = load_score_bundle(project_id, projects_root=projects_root)
    lint = lint_score(score)
    output = output_root / project_id
    raw, master, delivery = output / "raw-48k24.wav", output / "master-48k24.wav", output / f"{project_id}-44k16.wav"
    report_path = output / "render-report.json"
    if lint["status"] != "PASS":
        raise PianoBgmError("music lint FAIL: " + json.dumps(lint["errors"], ensure_ascii=False))
    if not all(path.is_file() for path in (raw, master, delivery, report_path)):
        raise PianoBgmError("render 산출물이 없음. 먼저 piano-bgm render를 실행해야 함")
    render_report = json.loads(report_path.read_text(encoding="utf-8"))
    raw_info, master_info, delivery_info = probe_audio(raw), probe_audio(master), probe_audio(delivery)
    loudness = measure_loudness(delivery)
    technical_ok = (
        raw_info["codec"] == "pcm_s24le" and raw_info["sampleRateHz"] == 48000 and raw_info["channels"] == 2
        and master_info["codec"] == "pcm_s24le" and master_info["sampleRateHz"] == 48000
        and delivery_info["codec"] == "pcm_s16le" and delivery_info["sampleRateHz"] == 44100 and delivery_info["channels"] == 2
        and delivery_info["bitsPerRawSample"] == 16 and abs(delivery_info["durationSec"] - request["durationSec"]) < .01
        and loudness["truePeakDbtp"] <= -0.5 and int(render_report.get("uniqueSampleCount", 0)) >= 8
    )
    instrument = instrument_record()
    status = "PENDING_USER_LISTENING" if technical_ok else "TECHNICAL_FAIL"
    provenance = {
        "schemaVersion": 1, "projectId": project_id, "status": status, "request": request,
        "scoreSha256": score["scoreSha256"], "performanceSha256": performance["performanceSha256"],
        "rendererSha256": sha256_file(REPO_ROOT / "pipeline" / "brushvid" / "piano_render.py"),
        "instrument": {"id": instrument["id"], "archiveSha256": instrument["acquisition"]["archiveSha256"], "license": instrument["license"]},
        "rawSha256": sha256_file(raw), "masterSha256": sha256_file(master), "deliverySha256": sha256_file(delivery),
        "musicLint": lint, "render": render_report, "rawTechnical": raw_info, "masterTechnical": master_info,
        "deliveryTechnical": delivery_info, "loudness": loudness,
        "source": "new deterministic score events rendered from local multi-sample piano only",
    }
    if engine_selection is not None:
        provenance["engineSelection"] = engine_selection
    output.mkdir(parents=True, exist_ok=True)
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa = {"schemaVersion": 1, "projectId": project_id, "status": status, "technicalPassed": technical_ok,
          "humanListening": {"status": "PENDING", "required": ["headphones", "laptop-speakers"]}, "provenance": "provenance.json"}
    (output / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    generated_manifest = {
        "schemaVersion": 1, "kind": "generated-piano-bgm", "assetId": project_id,
        "status": status, "approvalRequired": True, "distribution": request["output"]["distribution"],
        "localPath": _manifest_path(delivery), "durationSec": request["durationSec"],
        "format": {"sampleRateHz": 44100, "bitDepth": 16, "channels": 2},
        "provenance": "provenance.json", "qa": "qa.json",
        "license": instrument["license"],
        "policy": "Candidate only. Existing BGM catalog/automatic selection must not use this file until human listening approval and a separate catalog registration review.",
    }
    if engine_selection is not None:
        generated_manifest["engineSelection"] = engine_selection
    (output / "generated-bgm-manifest.json").write_text(json.dumps(generated_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Piano BGM — YouTube attribution", "", instrument["license"]["attributionText"],
             f"Changes: {instrument['license']['modificationNotice']}", "",
             f"Generated work: {score['presetTitle']} — original score events; {delivery}"]
    (output / "youtube-description.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return qa


def qa_generated_project(project_id: str, *, projects_root: Path = PROJECTS_ROOT,
                         output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    """Technical QA and provenance for a Stable Audio candidate.

    This deliberately does not reuse the sample-score checks: generated audio
    has no symbolic score or multi-sample render report. Both engines converge
    on the same pending human-listening and approval gate, however.
    """
    request_path = projects_root / project_id / "request.yaml"
    try:
        request = load_request(request_path)
    except PianoBgmError:
        raise
    output = output_root / project_id
    source = output / "source.wav"
    raw = output / "raw-48k24.wav"
    master = output / "master-48k24.wav"
    delivery = output / f"{project_id}-44k16.wav"
    generation_path = output / "generation.json"
    paths = (source, raw, master, delivery, generation_path)
    if not all(path.is_file() for path in paths):
        raise PianoBgmError("Stable Audio 산출물이 없음. 먼저 piano-bgm build 또는 generate를 실행해야 함")
    try:
        generation = json.loads(generation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PianoBgmError(f"Stable Audio generation.json 로드 실패: {generation_path}") from exc
    source_info, raw_info, master_info, delivery_info = tuple(probe_audio(path) for path in (source, raw, master, delivery))
    loudness = measure_loudness(delivery, target_i=-23.0, target_tp=-1.0)
    duration = float(request["durationSec"])
    source_sha256 = sha256_file(source)
    delivery_sha256 = sha256_file(delivery)
    generation_integrity = generation.get("sha256") == source_sha256
    technical_ok = (
        source_info["codec"] == "pcm_s16le" and source_info["sampleRateHz"] == 44100 and source_info["channels"] == 2
        and raw_info["codec"] == "pcm_s24le" and raw_info["sampleRateHz"] == 48000 and raw_info["channels"] == 2
        and master_info["codec"] == "pcm_s24le" and master_info["sampleRateHz"] == 48000 and master_info["channels"] == 2
        and delivery_info["codec"] == "pcm_s16le" and delivery_info["sampleRateHz"] == 44100
        and delivery_info["channels"] == 2 and delivery_info["bitsPerRawSample"] == 16
        and abs(source_info["durationSec"] - duration) < .02
        and abs(delivery_info["durationSec"] - duration) < .01
        and generation_integrity
        and abs(loudness["integratedLufs"] + 23.0) <= 1.5
        and loudness["truePeakDbtp"] <= -0.5
    )
    status = "PENDING_USER_LISTENING" if technical_ok else "TECHNICAL_FAIL"
    license_info = {
        "id": "stable-audio-community",
        "modelUrl": "https://huggingface.co/stabilityai/stable-audio-3-optimized",
        "termsUrl": "https://stability.ai/license",
        "commercialUse": "Subject to the current Stability AI license and account eligibility; verify before monetized release.",
        "attributionRequired": "Check the current license terms for the intended use.",
    }
    provenance = {
        "schemaVersion": 1, "projectId": project_id, "status": status, "request": request,
        "engine": "stable-audio-3-mlx", "generation": generation,
        "license": license_info,
        "sourceSha256": source_sha256, "generationSha256MatchesSource": generation_integrity,
        "rawSha256": sha256_file(raw),
        "masterSha256": sha256_file(master), "deliverySha256": delivery_sha256,
        "sourceTechnical": source_info, "rawTechnical": raw_info, "masterTechnical": master_info,
        "deliveryTechnical": delivery_info, "loudness": loudness,
        "source": "Stable Audio 3 MLX local candidate; technical QA does not establish legal clearance or musical quality",
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa = {"schemaVersion": 1, "projectId": project_id, "status": status, "technicalPassed": technical_ok,
          "humanListening": {"status": "PENDING", "required": ["headphones", "laptop-speakers"]},
          "engine": "stable-audio-3-mlx", "provenance": "provenance.json"}
    (output / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    generated_manifest = {
        "schemaVersion": 1, "kind": "generated-piano-bgm", "assetId": project_id,
        "status": status, "approvalRequired": True, "distribution": request["output"]["distribution"],
        "engine": "stable-audio-3-mlx", "localPath": _manifest_path(delivery), "durationSec": duration,
        "sourceSha256": source_sha256, "deliverySha256": delivery_sha256,
        "format": {"sampleRateHz": 44100, "bitDepth": 16, "channels": 2},
        "provenance": "provenance.json", "qa": "qa.json", "license": license_info,
        "policy": "Candidate only. Existing BGM catalog/automatic selection must not use this file until human listening approval and a separate catalog registration review.",
    }
    (output / "generated-bgm-manifest.json").write_text(json.dumps(generated_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Piano BGM — AI generation disclosure", "",
             "This candidate was generated locally with Stable Audio 3 MLX.",
             "Model: stabilityai/stable-audio-3-optimized",
             "License terms: https://stability.ai/license",
             "Model card: https://huggingface.co/stabilityai/stable-audio-3-optimized", "",
             "Human listening approval is required before monetized YouTube publication."]
    (output / "youtube-description.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return qa


def write_listening_review(project_id: str, *, output_root: Path = OUTPUT_ROOT) -> Path:
    output = output_root / project_id
    qa_path = output / "qa.json"
    if not qa_path.is_file():
        raise PianoBgmError("qa.json 없음. 먼저 qa를 실행해야 함")
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    delivery = output / f"{project_id}-44k16.wav"
    result_path = output / "listening-result.json"
    html = f"""<!doctype html><meta charset=\"utf-8\"><title>{project_id} listening review</title>
<h1>{project_id} — 피아노 BGM 사람 청취 승인</h1>
<p>현재 기술 상태: <strong>{qa['status']}</strong>. 이어폰과 노트북 스피커에서 모두 청취한 뒤 아래 JSON을 작성해 <code>piano-bgm approve</code>에 전달하세요.</p>
<audio controls src=\"{delivery.name}\"></audio>
<pre>{{\n  \"projectId\": \"{project_id}\",\n  \"headphones\": \"pass\",\n  \"laptopSpeakers\": \"pass\",\n  \"notes\": \"\"\n}}</pre>"""
    path = output / "listening-review.html"
    path.write_text(html, encoding="utf-8")
    result_path.write_text(json.dumps({"projectId": project_id, "headphones": "pending", "laptopSpeakers": "pending", "notes": ""}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def approve_listening(project_id: str, review_path: str | Path, *, output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    output = output_root / project_id
    qa_path = output / "qa.json"
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        review = json.loads(Path(review_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PianoBgmError("청취 승인 JSON 로드 실패") from exc
    if qa.get("status") != "PENDING_USER_LISTENING":
        raise PianoBgmError("기술 QA PENDING_USER_LISTENING 상태에서만 승인 가능")
    if review.get("projectId") != project_id or review.get("headphones") != "pass" or review.get("laptopSpeakers") != "pass":
        raise PianoBgmError("이어폰과 노트북 스피커 모두 pass인 review JSON이 필요함")
    qa["status"] = "APPROVED"
    qa["humanListening"] = {"status": "APPROVED", "review": Path(review_path).name, "notes": review.get("notes", "")}
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path = output / "generated-bgm-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "APPROVED"
        manifest["humanListening"] = qa["humanListening"]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return qa

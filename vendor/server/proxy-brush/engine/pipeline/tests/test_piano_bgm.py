from __future__ import annotations

import json
from pathlib import Path

import pytest

from brushvid.piano_bgm import (Key, PRESETS, SLEEP_ROUTINES, PianoBgmError, compose, lint_score,
                                normalize_request, performance_from_score, resolve_engine,
                                write_score_bundle, load_score_bundle)


def request(preset: str = "new-age", **overrides):
    data = {"projectId": f"test-{preset}", "kind": "piano-bgm", "durationSec": 30,
            "preset": preset, "seed": 123}
    data.update(overrides)
    return normalize_request(data)


def test_key_model_prevents_previous_tonic_base_errors():
    assert set(Key.parse("G-major").pitch_classes) == {7, 9, 11, 0, 2, 4, 6}  # F#; not F natural
    assert set(Key.parse("A-major").pitch_classes) == {9, 11, 1, 2, 4, 6, 8}
    assert set(Key.parse("D-lydian").pitch_classes) == {2, 4, 6, 8, 9, 11, 1}
    with pytest.raises(PianoBgmError, match="key"):
        Key.parse("F-sharp-major")


def test_schema_rejects_unknown_preset_and_invalid_duration():
    with pytest.raises(PianoBgmError, match="schema"):
        normalize_request({"projectId": "bad-preset", "kind": "piano-bgm", "durationSec": 30, "preset": "bossa"})
    with pytest.raises(PianoBgmError, match="schema"):
        normalize_request({"projectId": "too-short", "kind": "piano-bgm", "durationSec": 12, "preset": "new-age"})


def test_engine_defaults_preserve_sample_score_and_auto_selects_by_intent():
    legacy = request("cinematic-piano")
    assert legacy["engine"] == "sample-score"
    assert resolve_engine(legacy)["engine"] == "sample-score"

    featured = normalize_request({"projectId": "featured", "kind": "piano-bgm", "durationSec": 30,
                                  "preset": "cinematic-piano", "purpose": "featured", "engine": "auto"})
    assert resolve_engine(featured) == {"engine": "stable-audio-3-mlx", "reason": "featured-or-cinematic-preset"}

    sleep = normalize_request({"projectId": "sleep", "kind": "piano-bgm", "durationSec": 30,
                               "sleepRoutine": "lullaby", "engine": "auto"})
    assert resolve_engine(sleep)["engine"] == "sample-score"


def test_stable_audio_request_rejects_duration_over_model_limit():
    with pytest.raises(PianoBgmError, match="15~120"):
        normalize_request({"projectId": "too-long", "kind": "piano-bgm", "durationSec": 121,
                           "preset": "cinematic-piano", "engine": "stable-audio-3-mlx"})


@pytest.mark.parametrize("field,value", (("cfg", -0.1), ("cfg", 10.1), ("steps", 0), ("steps", 17)))
def test_stable_audio_sampling_bounds_are_schema_validated(field: str, value: float):
    with pytest.raises(PianoBgmError, match="schema"):
        normalize_request({"projectId": "bad-sampling", "kind": "piano-bgm", "durationSec": 30,
                           "preset": "cinematic-piano", "engine": "stable-audio-3-mlx", field: value})


def test_auto_long_request_falls_back_to_sample_score():
    long_request = normalize_request({"projectId": "long-feature", "kind": "piano-bgm", "durationSec": 121,
                                      "preset": "cinematic-piano", "purpose": "featured", "engine": "auto"})
    assert resolve_engine(long_request) == {"engine": "sample-score", "reason": "stable-audio-sm-music-max-120s"}


@pytest.mark.parametrize("preset", tuple(PRESETS))
@pytest.mark.parametrize("seconds", (15, 30, 60))
def test_every_preset_creates_exact_duration_harmony_aware_score(preset: str, seconds: int):
    score = compose(request(preset, durationSec=seconds))
    report = lint_score(score)
    assert score["durationSec"] == seconds
    assert abs(score["bars"] * score["barDurationSec"] - seconds) < 1e-5
    assert report["status"] == "PASS", report
    assert score["notes"]
    assert all(21 <= note["midi"] <= 108 for note in score["notes"])


@pytest.mark.parametrize("profile", tuple(SLEEP_ROUTINES))
def test_sleep_routine_option_selects_canonical_piano_contract(profile: str):
    normalized = normalize_request({"projectId": f"routine-{profile}", "kind": "piano-bgm", "durationSec": 30,
                                    "sleepRoutine": profile, "seed": 99})
    contract = SLEEP_ROUTINES[profile]
    assert normalized["preset"] == contract["preset"]
    assert normalized["key"] == contract["key"]
    assert normalized["tempoBpm"] == contract["tempoBpm"]
    score = compose(normalized)
    performance = performance_from_score(score)
    assert score["sleepRoutine"] == profile
    assert performance["performancePolicy"]["frequencyLayer"] == "none"
    assert performance["performancePolicy"]["electronicDrone"] is False
    assert performance["performancePolicy"]["binauralBeat"] is False
    assert lint_score(score)["status"] == "PASS"


def test_sleep_routine_rejects_conflicting_preset_and_generic_requires_preset():
    with pytest.raises(PianoBgmError, match="sleepRoutine=lullaby"):
        normalize_request({"projectId": "bad-routine", "kind": "piano-bgm", "durationSec": 30,
                           "sleepRoutine": "lullaby", "preset": "ambient-piano"})
    with pytest.raises(PianoBgmError, match="preset"):
        normalize_request({"projectId": "missing-preset", "kind": "piano-bgm", "durationSec": 30})


def test_score_is_deterministic_and_performance_consumes_roles():
    first, second = compose(request("fantasy-piano")), compose(request("fantasy-piano"))
    assert first["scoreSha256"] == second["scoreSha256"]
    performance = performance_from_score(first)
    assert performance["performancePolicy"]["accompanimentDucking"] is True
    assert performance["performancePolicy"]["randomVelocity"] is False
    assert {note["part"] for note in performance["notes"]} >= {"bass", "melody"}
    assert all("releaseSec" in note and "gain" in note for note in performance["notes"])
    featured = compose(request("fantasy-piano", purpose="featured"))
    featured_performance = performance_from_score(featured)
    background_melody = next(note for note in performance["notes"] if note["part"] == "melody")
    featured_melody = next(note for note in featured_performance["notes"] if note["part"] == "melody")
    assert background_melody["gain"] < featured_melody["gain"]


def test_scale_mismatch_regression_fixture_for_g_major_f_natural():
    score = compose(request("new-age"))
    target = next(note for note in score["notes"] if note["role"] == "melody")
    target["midi"] = 77  # F natural, intentionally invalid in G major.
    report = lint_score(score)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "scale-mismatch" for issue in report["errors"])


def test_strong_non_chord_tension_and_voice_crossing_are_rejected():
    score = compose(request("sleep-piano"))
    right = next(note for note in score["notes"] if note["role"] == "melody" and note["strength"] == "strong")
    right["midi"] = 62  # D: scale tone but non-chord tone over first C major chord.
    left = next(note for note in score["notes"] if note["hand"] == "left" and note["startSec"] == right["startSec"])
    left["midi"] = 80
    report = lint_score(score)
    codes = {issue["code"] for issue in report["errors"]}
    assert "strong-beat-non-chord" in codes
    assert "voice-crossing" in codes


def test_unresolved_tension_is_rejected_for_non_horror_preset():
    score = compose(request("sleep-piano"))
    tension = next(note for note in score["notes"] if note["role"] == "melody")
    tension["isTension"] = True
    for note in score["notes"]:
        if note["hand"] == "right" and note["startSec"] > tension["startSec"]:
            note["midi"] = 62  # D, not part of initial C-major chord.
    report = lint_score(score)
    assert any(issue["code"] == "unresolved-tension" for issue in report["errors"])


def test_write_and_load_bundle(tmp_path: Path):
    req = request("game-bgm-piano")
    score = compose(req)
    performance = performance_from_score(score)
    paths = write_score_bundle(req, score, performance, lint_score(score), projects_root=tmp_path)
    assert all(path.is_file() for path in paths.values())
    loaded_req, loaded_score, loaded_performance = load_score_bundle(req["projectId"], projects_root=tmp_path)
    assert loaded_req == req
    assert loaded_score["scoreSha256"] == score["scoreSha256"]
    assert loaded_performance["performanceSha256"] == performance["performanceSha256"]


def test_human_listening_approval_updates_candidate_manifest(tmp_path: Path):
    from brushvid.piano_bgm import approve_listening

    output = tmp_path / "approved-demo"
    output.mkdir()
    (output / "qa.json").write_text(json.dumps({
        "projectId": "approved-demo", "status": "PENDING_USER_LISTENING", "technicalPassed": True,
        "humanListening": {"status": "PENDING"},
    }), encoding="utf-8")
    (output / "generated-bgm-manifest.json").write_text(json.dumps({"assetId": "approved-demo", "status": "PENDING_USER_LISTENING"}), encoding="utf-8")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"projectId": "approved-demo", "headphones": "pass", "laptopSpeakers": "pass", "notes": "clean"}), encoding="utf-8")
    result = approve_listening("approved-demo", review, output_root=tmp_path)
    assert result["status"] == "APPROVED"
    assert json.loads((output / "generated-bgm-manifest.json").read_text())["status"] == "APPROVED"


def test_human_listening_rejection_does_not_promote_candidate(tmp_path: Path):
    from brushvid.piano_bgm import approve_listening

    output = tmp_path / "rejected-demo"
    output.mkdir()
    (output / "qa.json").write_text(json.dumps({"projectId": "rejected-demo", "status": "PENDING_USER_LISTENING"}), encoding="utf-8")
    review = tmp_path / "review-rejected.json"
    review.write_text(json.dumps({"projectId": "rejected-demo", "headphones": "pass", "laptopSpeakers": "reject"}), encoding="utf-8")
    with pytest.raises(PianoBgmError, match="이어폰과 노트북 스피커"):
        approve_listening("rejected-demo", review, output_root=tmp_path)
    assert json.loads((output / "qa.json").read_text()) ["status"] == "PENDING_USER_LISTENING"

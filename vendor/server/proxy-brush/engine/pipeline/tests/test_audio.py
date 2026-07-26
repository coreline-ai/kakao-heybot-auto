"""audio.py 테스트 — BGM 합성 + TC-4.E2(길이 불일치 자동 보정)."""
import wave

import pytest

from brushvid.audio import SR, probe_duration, reconcile_scenes_with_audio, synth_ambient_bgm


def test_synth_bgm_shape_and_determinism(tmp_path):
    """합성 BGM: 48kHz 스테레오, 요청 길이, 시드 결정적."""
    a = synth_ambient_bgm(tmp_path / "a.wav", 3.0, seed=5)
    b = synth_ambient_bgm(tmp_path / "b.wav", 3.0, seed=5)
    with wave.open(str(a)) as w:
        assert w.getnchannels() == 2
        assert w.getframerate() == SR
        assert w.getnframes() == int(SR * 3.0)
    assert a.read_bytes() == b.read_bytes()
    assert probe_duration(a) == pytest.approx(3.0, abs=0.05)


def test_tc_4_e2_reconcile_extends_last_scene():
    """TC-4.E2: 오디오가 씬 합산보다 1초 넘게 길면 경고+마지막 씬 연장."""
    scenes = [{"durationInFrames": 300, "cues": []}, {"durationInFrames": 300, "cues": []}]
    changed = reconcile_scenes_with_audio(scenes, audio_sec=25.0, fps=30)  # 750f vs 600f
    assert changed
    assert sum(s["durationInFrames"] for s in scenes) == 750


def test_tc_4_e2_within_tolerance_untouched():
    """1초 이내 오차는 보정하지 않는다."""
    scenes = [{"durationInFrames": 300, "cues": []}]
    changed = reconcile_scenes_with_audio(scenes, audio_sec=10.5, fps=30)  # 315f vs 300f (0.5s)
    assert not changed
    assert scenes[0]["durationInFrames"] == 300


def test_tc_4_e2_shrink_respects_cues():
    """오디오가 짧으면 축소하되 마지막 cue 끝 이하로는 줄이지 않는다."""
    scenes = [{"durationInFrames": 600, "cues": [{"text": "가", "from": 0, "to": 550}]}]
    reconcile_scenes_with_audio(scenes, audio_sec=10.0, fps=30)  # 300f 요청
    assert scenes[0]["durationInFrames"] == 556  # cue 끝(550)+6 하한

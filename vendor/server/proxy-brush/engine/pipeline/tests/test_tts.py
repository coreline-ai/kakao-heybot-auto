"""tts.py 테스트 — 문장 분리 / duration↔SRT 정합 / 미설치 에러 (전부 mock, 모델 불필요)."""
import wave

import numpy as np
import pytest

import brushvid.tts as tts_mod
from brushvid.cues import parse_srt
from brushvid.tts_engines.base import AudioResult
from brushvid.tts import SR, split_sentences, synthesize_narration


def _fake_synth(seconds_per_char=0.02):
    """문장 길이에 비례하는 가짜 wav 를 내는 합성기."""
    def synth(text: str) -> np.ndarray:
        n = max(1, int(SR * len(text) * seconds_per_char))
        return np.zeros(n, dtype=np.float32)
    return synth


def test_split_sentences_korean():
    text = "안녕하세요. 반갑습니다! 오늘 어때요? 마침표 없는 마지막 문장"
    assert split_sentences(text) == [
        "안녕하세요.", "반갑습니다!", "오늘 어때요?", "마침표 없는 마지막 문장"]


def test_duration_srt_wav_consistency(tmp_path):
    """문장별 duration 합 + pause = 최종 wav 길이 = SRT 마지막 타임스탬프."""
    text = "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다."
    res = synthesize_narration(text, tmp_path / "n.wav", tmp_path / "n.srt",
                               pause_ms=300, synth=_fake_synth())
    entries = res["entries"]
    assert len(entries) == 3
    # wav 실제 길이
    with wave.open(str(tmp_path / "n.wav")) as w:
        wav_sec = w.getnframes() / w.getframerate()
        assert w.getframerate() == SR
    # 문장 duration 합 + pause 2회
    dur_sum = sum(e["end"] - e["start"] for e in entries) + 0.3 * 2
    assert wav_sec == pytest.approx(dur_sum, abs=1e-3)
    assert res["durationSec"] == pytest.approx(wav_sec, abs=1e-3)
    # SRT 마지막 타임스탬프 = 마지막 문장 end (pause 는 문장 사이에만)
    parsed = parse_srt((tmp_path / "n.srt").read_text(encoding="utf-8"))
    assert len(parsed) == 3
    assert parsed[-1].end == pytest.approx(entries[-1]["end"], abs=2e-3)
    assert parsed[-1].end == pytest.approx(wav_sec, abs=2e-3)
    # 문장 사이 pause 반영 확인
    assert parsed[1].start == pytest.approx(parsed[0].end + 0.3, abs=2e-3)


def test_missing_supertonic_clear_error(tmp_path, monkeypatch):
    """supertonic 미설치 → 설치 명령 포함 명확한 에러 (침묵 폴백 금지)."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "supertonic":
            raise ImportError("No module named 'supertonic'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match=r'pip install -e "pipeline\[tts\]"'):
        synthesize_narration("한 문장.", tmp_path / "x.wav", tmp_path / "x.srt")


def test_unknown_engine_rejected(tmp_path):
    with pytest.raises(ValueError, match="엔진"):
        synthesize_narration("한 문장.", tmp_path / "x.wav", tmp_path / "x.srt",
                             engine="elevenlabs", synth=_fake_synth())


def test_empty_text_rejected(tmp_path):
    with pytest.raises(ValueError, match="문장"):
        synthesize_narration("   \n ", tmp_path / "x.wav", tmp_path / "x.srt",
                             synth=_fake_synth())


def test_injected_synth_still_resolves_canonical_voice_metadata(tmp_path):
    res = synthesize_narration(
        "전문 해설입니다.", tmp_path / "x.wav", tmp_path / "x.srt",
        voice="female-09", speed=1.10, synth=_fake_synth(),
    )
    assert res["voice"]["requestedVoice"] == "female-09"
    assert res["voice"]["voicePresetId"] == "female-09"
    assert res["voice"]["components"] == {"F4": 0.65, "F1": 0.35}
    assert res["voice"]["speed"] == pytest.approx(1.10)


@pytest.mark.parametrize("speed", [0.69, 2.01, float("nan"), float("inf"), True, "fast"])
def test_invalid_speed_rejected_by_direct_api(tmp_path, speed):
    with pytest.raises(ValueError, match="speed"):
        synthesize_narration(
            "한 문장.", tmp_path / "x.wav", tmp_path / "x.srt",
            speed=speed, synth=_fake_synth(),
        )


def test_make_synthesizer_forwards_speed_and_resolved_style(monkeypatch):
    calls = {}

    class FakeTTS:
        model_name = "supertonic-3"
        sample_rate = SR

        def __init__(self, auto_download):
            assert auto_download is True

        def synthesize(self, text, *, voice_style, lang, speed):
            calls.update(text=text, voice_style=voice_style, lang=lang, speed=speed)
            return np.zeros((1, 10), dtype=np.float32), np.array([10 / SR])

    class FakeModule:
        __version__ = "1.3.1"
        TTS = FakeTTS

    style = object()
    metadata = {"voicePresetId": "female-09", "components": {"F4": 0.65, "F1": 0.35}}
    monkeypatch.setattr(tts_mod, "_import_supertonic", lambda: FakeModule)
    monkeypatch.setattr(tts_mod, "build_voice_style", lambda *_a, **_k: (style, metadata.copy()))
    synth, resolved = tts_mod._make_synthesizer("female-09", "ko", 1.10)
    assert synth("테스트").shape == (10,)
    assert calls == {"text": "테스트", "voice_style": style, "lang": "ko", "speed": 1.10}
    assert resolved["voicePresetId"] == "female-09"
    assert resolved["packageVersion"] == "1.3.1"


def test_qwen_speed_uses_common_atempo_postprocess(tmp_path, monkeypatch):
    class FakeQwen:
        def __init__(self, **kwargs):
            assert kwargs["reference"] == {"audio": tmp_path / "ref.wav", "transcript": tmp_path / "ref.txt"}

        def synthesize_batch(self, sentences, **kwargs):
            assert sentences == ["한 문장."]
            assert kwargs["speed"] == pytest.approx(2.0)
            return [AudioResult(
                np.zeros(24000, dtype=np.float32), 24000,
                {"engine": "qwen3-base", "model": "qwen", "speed": 2.0},
            )]

        def close(self):
            pass

    reference = {"audio": tmp_path / "ref.wav", "transcript": tmp_path / "ref.txt"}
    monkeypatch.setattr(tts_mod, "QwenAdapter", FakeQwen)
    result = synthesize_narration(
        "한 문장.", tmp_path / "q.wav", tmp_path / "q.srt",
        engine="qwen3-base", voice="f1-reference", speed=2.0,
        reference=reference,
    )
    with wave.open(str(tmp_path / "q.wav"), "rb") as wav_file:
        assert wav_file.getframerate() == SR
        assert wav_file.getnframes() == pytest.approx(SR / 2, abs=SR * 0.03)
    assert result["voice"]["speedAppliedBy"] == "ffmpeg-atempo"

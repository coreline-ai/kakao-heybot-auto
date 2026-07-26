"""stt.py 테스트 — faster-whisper 백엔드 (미설치 에러 mock + SRT 포맷 회귀)."""
import builtins

import pytest

from brushvid.cues import parse_srt, srt_to_cues
from brushvid.stt import transcribe


def test_srt_format_regression(tmp_path):
    """세그먼트 → SRT 포맷이 cues.py 계약(parse_srt/srt_to_cues)과 호환."""
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake")

    def fake_segments(_p):
        return [(0.0, 5.0, "안녕하세요 오늘은 테스트입니다."),
                (5.0, 8.84, "두 번째 세그먼트."),
                (8.84, 10.24, "")]  # 빈 텍스트는 제외되어야 함

    srt = transcribe(audio, tmp_path / "stt", segments_fn=fake_segments)
    assert srt.name == "voice.srt"
    entries = parse_srt(srt.read_text(encoding="utf-8"))
    assert len(entries) == 2
    assert entries[0].text == "안녕하세요 오늘은 테스트입니다."
    assert entries[1].end == pytest.approx(8.84)
    cues = srt_to_cues(srt.read_text(encoding="utf-8"), fps=30)
    assert cues[0]["from"] == 0 and cues[0]["to"] == 150  # 5.0s → 150f


def test_missing_faster_whisper_clear_error(tmp_path, monkeypatch):
    """faster-whisper 미설치 → 설치 명령 포함 명확한 에러 (침묵 폴백 금지)."""
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake")
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match=r'pip install -e "pipeline\[stt\]"'):
        transcribe(audio, tmp_path / "stt")

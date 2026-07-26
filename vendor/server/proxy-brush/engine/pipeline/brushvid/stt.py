"""stt.py — 더빙 오디오 → faster-whisper(small, ko) → SRT.

pipeline/.venv 자체 의존만 사용한다 (외부 venv 의존 없음).
faster-whisper 미설치 시 침묵 폴백 없이 설치 명령을 담은 명확한 에러를 낸다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .tts import format_srt_time

log = logging.getLogger(__name__)

INSTALL_HINT = 'STT 사용을 위해 faster-whisper 가 필요합니다. 설치: pipeline/.venv/bin/pip install -e "pipeline[stt]"'


def _import_faster_whisper():
    """faster-whisper 임포트 — 미설치 시 설치 명령 포함 에러 (침묵 폴백 금지)."""
    try:
        import faster_whisper
        return faster_whisper
    except ImportError as e:
        raise RuntimeError(INSTALL_HINT) from e


def transcribe(audio_path: str | Path, out_dir: str | Path, *, model: str = "small",
               language: str = "ko", model_root: str | Path | None = None,
               segments_fn=None) -> Path:
    """faster-whisper 로 SRT 생성. 반환: 생성된 SRT 경로.

    segments_fn 은 테스트 주입용 — audio 경로를 받아 (start, end, text) 목록을 반환.
    """
    audio_path = Path(audio_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if segments_fn is None:
        fw = _import_faster_whisper()
        if model_root is None:
            raise RuntimeError("MODEL_MISSING: faster-whisper local model root가 필요합니다")
        local_root = Path(model_root).expanduser().resolve()
        if not local_root.is_dir():
            raise RuntimeError("MODEL_MISSING: faster-whisper local model root가 없습니다")
        wm = fw.WhisperModel(
            model, device="cpu", compute_type="int8",
            download_root=str(local_root), local_files_only=True,
        )
        log.info("faster-whisper(%s, %s) 전사 시작: %s", model, language, audio_path.name)
        raw_segments, info = wm.transcribe(str(audio_path), language=language)
        segments = [(s.start, s.end, s.text.strip()) for s in raw_segments]
        log.info("전사 완료: %d 세그먼트, 오디오 %.1fs", len(segments), info.duration)
    else:
        segments = list(segments_fn(audio_path))

    srt = out_dir / f"{audio_path.stem}.srt"
    blocks = []
    for i, (start, end, text) in enumerate(segments, 1):
        if not text:
            continue
        blocks.append(f"{i}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n")
    srt.write_text("\n".join(blocks), encoding="utf-8")
    if not blocks:
        log.warning("전사 결과가 비어 있음: %s", audio_path)
    return srt

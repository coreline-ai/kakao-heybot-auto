"""Qwen3-TTS Base protocol v1 worker.

모델 의존성은 worker process 안에서만 import한다. stdout에는 protocol JSON만
출력하고, 로그·경고는 stderr로 보낸다.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import time
import traceback
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..tts_contract import ENGINE_MODEL_IDS, MODEL_REVISIONS, resolve_local_snapshot


PROTOCOL_VERSION = 1
ENGINE_ID = "qwen3-base"
LANGUAGE = "Korean"
STARTUP_TIMEOUT_SEC = 300
GENERATION_TIMEOUT_SEC = 600
SHUTDOWN_GRACE_SEC = 5
ERROR_CODES = {
    "DEPENDENCY_MISSING", "MODEL_MISSING", "INVALID_REQUEST", "REFERENCE_MISMATCH",
    "STARTUP_TIMEOUT", "GENERATION_TIMEOUT", "OOM", "CANCELLED", "PROTOCOL_ERROR", "MODEL_ERROR",
}


class WorkerProtocolError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        if code not in ERROR_CODES:
            code = "PROTOCOL_ERROR"
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_file(value: Any, root: Path, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise WorkerProtocolError("INVALID_REQUEST", f"{label}는 비어 있지 않은 경로여야 함")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise WorkerProtocolError("PROTOCOL_ERROR", f"{label} 절대 경로 금지")
    raw_candidate = root / path
    if raw_candidate.is_symlink():
        raise WorkerProtocolError("PROTOCOL_ERROR", f"{label} symlink 금지")
    candidate = raw_candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkerProtocolError("PROTOCOL_ERROR", f"{label}가 work root 밖을 가리킴") from exc
    if not candidate.is_file():
        raise WorkerProtocolError("INVALID_REQUEST", f"{label} regular file 없음: {candidate}")
    return candidate


def _relative_output_dir(value: Any, root: Path) -> Path:
    if (
        not isinstance(value, str) or not value.strip()
        or Path(value).is_absolute() or ".." in Path(value).parts
    ):
        raise WorkerProtocolError("PROTOCOL_ERROR", "outputDir는 work root 기준 상대 경로여야 함")
    raw_output = root / value
    if raw_output.is_symlink():
        raise WorkerProtocolError("PROTOCOL_ERROR", "outputDir symlink 금지")
    output = raw_output.resolve()
    try:
        output.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkerProtocolError("PROTOCOL_ERROR", "outputDir가 work root 밖을 가리킴") from exc
    output.mkdir(parents=True, exist_ok=True)
    return output


def _validate_request(request: Any, root: Path) -> tuple[str, list[dict], Path, Path, str]:
    if not isinstance(request, dict):
        raise WorkerProtocolError("INVALID_REQUEST", "request는 JSON object여야 함")
    if request.get("protocolVersion") != PROTOCOL_VERSION:
        raise WorkerProtocolError("PROTOCOL_ERROR", "protocolVersion 불일치")
    request_id = request.get("requestId")
    if not isinstance(request_id, str) or not request_id.strip():
        raise WorkerProtocolError("INVALID_REQUEST", "requestId가 없음")
    if request.get("engine") != ENGINE_ID:
        raise WorkerProtocolError("INVALID_REQUEST", "engine이 qwen3-base가 아님")
    if request.get("modelRevision") != MODEL_REVISIONS[ENGINE_ID]:
        raise WorkerProtocolError("MODEL_MISSING", "Qwen model revision 불일치")
    if request.get("language") != LANGUAGE:
        raise WorkerProtocolError("INVALID_REQUEST", "language는 Korean이어야 함")
    sentences = request.get("sentences")
    if not isinstance(sentences, list) or not sentences:
        raise WorkerProtocolError("INVALID_REQUEST", "sentences가 비어 있음")
    ids: list[str] = []
    for sentence in sentences:
        if not isinstance(sentence, dict) or not isinstance(sentence.get("id"), str):
            raise WorkerProtocolError("INVALID_REQUEST", "sentence id가 없음")
        if sentence["id"] in ids:
            raise WorkerProtocolError("PROTOCOL_ERROR", "sentence id 중복")
        if not isinstance(sentence.get("text"), str) or not sentence["text"].strip():
            raise WorkerProtocolError("INVALID_REQUEST", "sentence text가 비어 있음")
        ids.append(sentence["id"])
    reference = request.get("reference")
    if not isinstance(reference, dict) or request.get("xVectorOnlyMode") is not False:
        raise WorkerProtocolError("INVALID_REQUEST", "명시적 reference pair와 xVectorOnlyMode=false가 필요함")
    audio = _relative_file(reference.get("audio"), root, label="reference.audio")
    transcript = _relative_file(reference.get("transcript"), root, label="reference.transcript")
    if not transcript.read_text(encoding="utf-8").strip():
        raise WorkerProtocolError("INVALID_REQUEST", "reference.transcript가 비어 있음")
    for key, path in (("audioSha256", audio), ("transcriptSha256", transcript)):
        expected = reference.get(key)
        if not isinstance(expected, str) or _sha256(path) != expected:
            raise WorkerProtocolError("REFERENCE_MISMATCH", f"{key} 불일치")
    return request_id, sentences, _relative_output_dir(request.get("outputDir"), root), audio, transcript


def _emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def handle_request(model: Any, request: dict[str, Any], work_root: str | Path) -> dict[str, Any]:
    root = Path(work_root).resolve()
    request_id, sentences, output_dir, audio, transcript = _validate_request(request, root)
    ref_text = transcript.read_text(encoding="utf-8").strip()
    try:
        # 모델·토크나이저 progress 출력도 protocol stdout을 오염시키지 않는다.
        with contextlib.redirect_stdout(sys.stderr):
            prompt = model.create_voice_clone_prompt(
                ref_audio=str(audio), ref_text=ref_text, x_vector_only_mode=False,
            )
            texts = [sentence["text"] for sentence in sentences]
            wavs, sample_rate = model.generate_voice_clone(
                text=texts,
                language=[LANGUAGE] * len(texts),
                voice_clone_prompt=prompt,
                non_streaming_mode=True,
            )
    except MemoryError as exc:
        raise WorkerProtocolError("OOM", "Qwen model memory 부족") from exc
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        raise WorkerProtocolError("MODEL_ERROR", message) from exc
    if not isinstance(wavs, (list, tuple)) or len(wavs) != len(sentences):
        raise WorkerProtocolError("PROTOCOL_ERROR", "Qwen output sentence 수 불일치")
    try:
        sample_rate = int(sample_rate)
    except (TypeError, ValueError) as exc:
        raise WorkerProtocolError("PROTOCOL_ERROR", "Qwen sample rate가 유효하지 않음") from exc
    if sample_rate <= 0:
        raise WorkerProtocolError("PROTOCOL_ERROR", "Qwen sample rate가 양수가 아님")
    outputs = []
    for index, (sentence, wav) in enumerate(zip(sentences, wavs, strict=True)):
        if hasattr(wav, "detach"):
            wav = wav.detach().cpu().numpy()
        array = np.asarray(wav, dtype=np.float32).reshape(-1)
        if array.size == 0 or not np.isfinite(array).all() or float(np.max(np.abs(array))) > 1.0 + 1e-6:
            raise WorkerProtocolError("MODEL_ERROR", f"sentence {sentence['id']} waveform이 유효하지 않음")
        filename = f"sentence-{index:04d}.wav"
        destination = output_dir / filename
        temporary = output_dir / f".{filename}.tmp"
        with wave.open(str(temporary), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes((np.clip(array, -1.0, 1.0) * 32767).astype("<i2").tobytes())
        temporary.replace(destination)
        outputs.append({
            "id": sentence["id"], "filename": filename,
            "sampleRate": sample_rate, "durationSec": len(array) / sample_rate,
        })
    return {
        "protocolVersion": PROTOCOL_VERSION, "requestId": request_id, "ok": True,
        "outputs": outputs, "referencePromptBuilds": 1,
    }


def _load_model(model_dir: Path, device: str) -> Any:
    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise WorkerProtocolError("DEPENDENCY_MISSING", "qwen-tts 또는 torch가 설치되지 않음") from exc
    device_map = {"cuda": "cuda:0", "cpu": "cpu", "mps": "mps"}.get(device, "cpu")
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    return Qwen3TTSModel.from_pretrained(
        str(model_dir), device_map=device_map, dtype=dtype, local_files_only=True,
    )


def run_worker(
    *,
    model_dir: str | Path,
    work_root: str | Path,
    device: str = "cpu",
    model_loader: Callable[[Path, str], Any] = _load_model,
) -> int:
    try:
        snapshot = resolve_local_snapshot(
            ENGINE_MODEL_IDS[ENGINE_ID], MODEL_REVISIONS[ENGINE_ID],
            explicit_dir=model_dir,
        )
        with contextlib.redirect_stdout(sys.stderr):
            model = model_loader(snapshot, device)
    except WorkerProtocolError as exc:
        _emit({"protocolVersion": PROTOCOL_VERSION, "ready": False,
               "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}})
        return 1
    except FileNotFoundError as exc:
        _emit({"protocolVersion": PROTOCOL_VERSION, "ready": False,
               "error": {"code": "MODEL_MISSING", "message": str(exc), "retryable": False}})
        return 1
    except MemoryError as exc:
        _emit({"protocolVersion": PROTOCOL_VERSION, "ready": False,
               "error": {"code": "OOM", "message": "Qwen model memory 부족", "retryable": False}})
        return 1
    except Exception as exc:
        _emit({"protocolVersion": PROTOCOL_VERSION, "ready": False,
               "error": {"code": "MODEL_ERROR", "message": f"{type(exc).__name__}: {exc}", "retryable": False}})
        return 1
    _emit({"protocolVersion": PROTOCOL_VERSION, "ready": True,
           "modelRevision": MODEL_REVISIONS[ENGINE_ID]})
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            if isinstance(request, dict) and request.get("command") == "cancel":
                response = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "requestId": request.get("requestId"),
                    "ok": False,
                    "error": {"code": "CANCELLED", "message": "worker 취소 요청", "retryable": False},
                }
                _emit(response)
                break
            response = handle_request(model, request, work_root)
        except json.JSONDecodeError as exc:
            response = {"protocolVersion": PROTOCOL_VERSION, "ok": False,
                        "error": {"code": "PROTOCOL_ERROR", "message": str(exc), "retryable": False}}
        except WorkerProtocolError as exc:
            response = {"protocolVersion": PROTOCOL_VERSION,
                        "requestId": request.get("requestId") if isinstance(request, dict) else None,
                        "ok": False,
                        "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable}}
        except Exception as exc:  # protocol must remain JSON even on an unexpected bug
            traceback.print_exc(file=sys.stderr)
            response = {"protocolVersion": PROTOCOL_VERSION, "ok": False,
                        "error": {"code": "MODEL_ERROR", "message": f"{type(exc).__name__}: {exc}", "retryable": False}}
        _emit(response)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--device", default="cpu")
    args = parser.parse_args()
    return run_worker(model_dir=args.model_dir, work_root=args.work_root, device=args.device)


if __name__ == "__main__":
    raise SystemExit(main())

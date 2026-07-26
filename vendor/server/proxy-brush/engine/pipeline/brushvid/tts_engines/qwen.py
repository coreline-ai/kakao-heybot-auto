"""Qwen3-TTS Base worker client and batch adapter."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np

from ..tts_contract import (
    ENGINE_LICENSES,
    ENGINE_MODEL_IDS,
    MODEL_REVISIONS,
    resolve_local_snapshot,
)
from .base import AudioResult, TtsEngineError, TtsEngineUnavailableError
from .qwen3_worker import (
    GENERATION_TIMEOUT_SEC,
    LANGUAGE,
    PROTOCOL_VERSION,
    SHUTDOWN_GRACE_SEC,
    STARTUP_TIMEOUT_SEC,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class QwenWorkerClient:
    def __init__(
        self,
        *,
        python_path: str | Path,
        model_dir: Path,
        work_root: Path,
        device: str,
        startup_timeout: float = STARTUP_TIMEOUT_SEC,
        generation_timeout: float = GENERATION_TIMEOUT_SEC,
    ) -> None:
        self.python_path = str(python_path)
        self.model_dir = model_dir
        self.work_root = work_root
        self.device = device
        self.startup_timeout = startup_timeout
        self.generation_timeout = generation_timeout
        self.process: subprocess.Popen[str] | None = None

    def _read_json(self, timeout: float) -> dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            raise TtsEngineError("Qwen worker가 시작되지 않음")
        selector = selectors.DefaultSelector()
        try:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            events = selector.select(timeout)
            if not events:
                raise TimeoutError("worker 응답 timeout")
            line = self.process.stdout.readline()
        finally:
            selector.close()
        if not line:
            raise TtsEngineError(f"Qwen worker가 종료됨: exit={self.process.poll()}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TtsEngineError("Qwen worker stdout가 JSON이 아님") from exc
        if not isinstance(value, dict):
            raise TtsEngineError("Qwen worker 응답이 object가 아님")
        return value

    def start(self) -> None:
        self.work_root.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({"PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        repo_root = Path(__file__).resolve().parents[2]
        existing_path = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(repo_root), existing_path]))
        command = [
            self.python_path, "-m", "brushvid.tts_engines.qwen3_worker",
            "--model-dir", str(self.model_dir), "--work-root", str(self.work_root),
            "--device", self.device,
        ]
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
                start_new_session=(os.name == "posix"),
            )
            ready = self._read_json(self.startup_timeout)
        except TimeoutError as exc:
            self.close()
            raise TtsEngineUnavailableError("STARTUP_TIMEOUT: Qwen worker startup timeout") from exc
        except OSError as exc:
            self.close()
            raise TtsEngineUnavailableError(f"DEPENDENCY_MISSING: Qwen worker 실행 실패: {exc}") from exc
        if ready.get("protocolVersion") != PROTOCOL_VERSION or ready.get("ready") is not True:
            self.close()
            error = ready.get("error") or {}
            raise TtsEngineUnavailableError(
                f"{error.get('code', 'MODEL_ERROR')}: {error.get('message', 'worker ready 실패')}"
            )
        if ready.get("modelRevision") != MODEL_REVISIONS["qwen3-base"]:
            self.close()
            raise TtsEngineError("Qwen worker model revision 불일치")

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.process is None:
            self.start()
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        try:
            response = self._read_json(self.generation_timeout)
        except TimeoutError as exc:
            self.close()
            raise TtsEngineError("GENERATION_TIMEOUT: Qwen worker generation timeout") from exc
        if response.get("protocolVersion") != PROTOCOL_VERSION:
            raise TtsEngineError("PROTOCOL_ERROR: Qwen worker protocol version 불일치")
        if response.get("requestId") != payload.get("requestId"):
            raise TtsEngineError("PROTOCOL_ERROR: Qwen worker requestId 불일치")
        if response.get("ok") is not True:
            error = response.get("error") or {}
            raise TtsEngineError(f"{error.get('code', 'MODEL_ERROR')}: {error.get('message', 'worker 실패')}")
        return response

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=SHUTDOWN_GRACE_SEC)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=SHUTDOWN_GRACE_SEC)

    def cancel(self) -> None:
        """취소 시 worker process group을 즉시 종료하고 graceful grace를 적용한다."""
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=SHUTDOWN_GRACE_SEC)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait(timeout=SHUTDOWN_GRACE_SEC)


class QwenAdapter:
    engine_id = "qwen3-base"

    def __init__(
        self,
        *,
        reference: dict[str, Path],
        model_dir: str | Path | None = None,
        python_path: str | Path | None = None,
        device: str = "cpu",
        worker_client_factory=QwenWorkerClient,
        work_root: str | Path | None = None,
    ) -> None:
        self.request_id = f"tts-{uuid.uuid4().hex}"
        temp_parent = None
        if work_root is not None:
            temp_parent = Path(work_root).expanduser().resolve()
            temp_parent.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(prefix="qwen-", dir=temp_parent)
        self.work_root = Path(self._temp.name)
        self._stage_reference(reference)
        self.model_dir = resolve_local_snapshot(
            ENGINE_MODEL_IDS[self.engine_id], MODEL_REVISIONS[self.engine_id],
            explicit_dir=model_dir,
        )
        selected_python = python_path or os.environ.get("BRUSHVID_QWEN_PYTHON") or sys.executable
        self.client = worker_client_factory(
            python_path=selected_python, model_dir=self.model_dir,
            work_root=self.work_root, device=device,
        )
        self.metadata = {
            "engine": self.engine_id, "model": ENGINE_MODEL_IDS[self.engine_id],
            "modelRevision": MODEL_REVISIONS[self.engine_id], "language": "ko",
            "packageVersion": "qwen-tts==0.1.1", "referenceVoiceId": "",
            "xVectorOnlyMode": False,
            "license": {**ENGINE_LICENSES[self.engine_id], "aiDisclosureRequired": True},
        }

    def _stage_reference(self, reference: dict[str, Path]) -> None:
        if set(reference) != {"audio", "transcript"}:
            raise ValueError("Qwen reference는 audio/transcript pair가 필요함")
        destination = self.work_root / "reference"
        destination.mkdir()
        for key, source in reference.items():
            source = Path(source)
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Qwen reference {key} regular file 없음")
            target = destination / ("reference.wav" if key == "audio" else "reference.txt")
            temporary = target.with_name(f".{target.name}.tmp")
            try:
                shutil.copyfile(source, temporary)
                if _sha256(source) != _sha256(temporary):
                    raise TtsEngineError(f"Qwen reference {key} staging hash 불일치")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)

    def synthesize_batch(
        self,
        sentences: list[str],
        *,
        voice: str,
        language: str,
        speed: float,
    ) -> list[AudioResult]:
        if language != "ko":
            raise ValueError("qwen3-base language는 ko만 지원함")
        if not sentences:
            raise ValueError("Qwen sentences가 비어 있음")
        try:
            batch_size = int(os.environ.get("BRUSHVID_QWEN_BATCH_SIZE", "8"))
        except ValueError as exc:
            raise ValueError("BRUSHVID_QWEN_BATCH_SIZE는 양의 정수여야 함") from exc
        if batch_size < 1:
            raise ValueError("BRUSHVID_QWEN_BATCH_SIZE는 양의 정수여야 함")
        self.metadata["referenceVoiceId"] = voice
        audio = self.work_root / "reference" / "reference.wav"
        transcript = self.work_root / "reference" / "reference.txt"
        reference = {
            "audio": "reference/reference.wav", "transcript": "reference/reference.txt",
            "audioSha256": _sha256(audio), "transcriptSha256": _sha256(transcript),
        }
        results: list[AudioResult] = []
        try:
            # 긴 대본은 작은 chunk로 나눠 GPU/통합 메모리 폭증을 막되, worker는 한 번만
            # 기동한다. 즉 모델·reference는 계속 유지되고 output 순서도 원본 그대로다.
            for chunk_index, start in enumerate(range(0, len(sentences), batch_size)):
                chunk = sentences[start:start + batch_size]
                request = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "requestId": f"{self.request_id}-{chunk_index:03d}",
                    "engine": self.engine_id,
                    "modelRevision": MODEL_REVISIONS[self.engine_id],
                    "language": LANGUAGE,
                    "xVectorOnlyMode": False,
                    "reference": reference,
                    "sentences": [{"id": f"s{index + 1}", "text": text}
                                  for index, text in enumerate(chunk)],
                    "outputDir": f"outputs/chunk-{chunk_index:03d}",
                }
                response = self.client.request(request)
                outputs = response.get("outputs")
                if not isinstance(outputs, list) or len(outputs) != len(chunk):
                    raise TtsEngineError("PROTOCOL_ERROR: Qwen output 수 불일치")
                output_root = (self.work_root / request["outputDir"]).resolve()
                for expected_index, item in enumerate(outputs):
                    if (
                        not isinstance(item, dict)
                        or item.get("id") != f"s{expected_index + 1}"
                        or not isinstance(item.get("filename"), str)
                        or Path(item["filename"]).is_absolute()
                    ):
                        raise TtsEngineError("PROTOCOL_ERROR: Qwen output 순서/경로 불일치")
                    path = (output_root / item["filename"]).resolve()
                    try:
                        path.relative_to(output_root)
                    except ValueError as exc:
                        raise TtsEngineError("PROTOCOL_ERROR: Qwen output 경로가 work root 밖") from exc
                    if not path.is_file():
                        raise TtsEngineError("PROTOCOL_ERROR: Qwen output 파일 없음")
                    with wave.open(str(path), "rb") as wav:
                        raw = wav.readframes(wav.getnframes())
                        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0
                        results.append(AudioResult(samples, wav.getframerate(), {
                            **self.metadata, "nativeSampleRate": wav.getframerate(), "speed": speed,
                        }))
            return results
        finally:
            self.close()

    def close(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def cancel(self) -> None:
        """합성 취소 시 worker와 controlled reference/output을 함께 폐기한다."""
        try:
            self.client.cancel()
        finally:
            self._temp.cleanup()

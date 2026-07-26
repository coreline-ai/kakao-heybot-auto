from __future__ import annotations

import json
import hashlib
import io
import os
import signal
import subprocess
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator, FormatChecker

from brushvid.tts_contract import (
    ENGINE_IDS,
    cache_signature_material,
    normalize_language,
    tts_cache_signature,
    validate_pause_ms,
    validate_reference,
    validate_speed,
)
from brushvid.tts_engines.base import AudioResult, TtsEngineError, TtsEngineUnavailableError
from brushvid.tts_engines.melo import MeloAdapter
from brushvid.tts_engines.qwen3_worker import (
    MODEL_REVISIONS,
    WorkerProtocolError,
    handle_request,
    run_worker,
)
from brushvid.tts_engines import qwen as qwen_client
from brushvid.tts_engines.qwen import QwenAdapter, QwenWorkerClient
from brushvid.tts_engines.registry import create_engine, register_engine, supported_engines


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schema" / "tts-voice-manifest.schema.json"


def test_engine_ids_and_audio_result_contract():
    assert ENGINE_IDS == ("supertonic", "melo-ko", "qwen3-base")
    result = AudioResult(np.array([0.0, 1.0 + 5e-7, -1.0], dtype=np.float64), 24000)
    assert result.samples.dtype == np.float32
    assert result.metadata["nativeClampCount"] == 1
    with pytest.raises(TtsEngineError, match="NaN"):
        AudioResult(np.array([np.nan], dtype=np.float32), 24000)
    with pytest.raises(TtsEngineError, match="범위 초과"):
        AudioResult(np.array([1.01], dtype=np.float32), 24000)


@pytest.mark.parametrize("value", [0.70, 2.0])
def test_speed_boundaries(value):
    assert validate_speed(value) == value


@pytest.mark.parametrize("value", [True, "1.0", float("nan"), float("inf"), 0.69, 2.01])
def test_speed_invalid(value):
    with pytest.raises(ValueError, match="speed"):
        validate_speed(value)


@pytest.mark.parametrize("value", [True, 1.5, "300", -1])
def test_pause_requires_non_negative_integer(value):
    with pytest.raises(ValueError, match="pauseMs"):
        validate_pause_ms(value)


def test_new_engines_are_korean_only():
    assert normalize_language("melo-ko", "KO") == "ko"
    assert normalize_language("qwen3-base", "ko") == "ko"
    with pytest.raises(ValueError, match="language=ko"):
        normalize_language("qwen3-base", "en")


def test_reference_is_project_local_regular_pair(tmp_path):
    audio = tmp_path / "ref.wav"
    transcript = tmp_path / "ref.txt"
    audio.write_bytes(b"RIFF")
    transcript.write_text("기준 음성입니다.", encoding="utf-8")
    pair = validate_reference({"audio": "ref.wav", "transcript": "ref.txt"}, base_dir=tmp_path)
    assert pair == {"audio": audio.resolve(), "transcript": transcript.resolve()}
    with pytest.raises(ValueError, match="프로젝트 밖"):
        validate_reference({"audio": "../ref.wav", "transcript": "ref.txt"}, base_dir=tmp_path)
    transcript.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="비어 있음"):
        validate_reference({"audio": "ref.wav", "transcript": "ref.txt"}, base_dir=tmp_path)


def test_reference_symlink_is_rejected(tmp_path):
    audio = tmp_path / "ref.wav"
    transcript = tmp_path / "ref.txt"
    audio.write_bytes(b"RIFF")
    transcript.write_text("기준 문장", encoding="utf-8")
    link = tmp_path / "link.wav"
    try:
        link.symlink_to(audio)
    except OSError:
        pytest.skip("symlink를 만들 수 없는 환경")
    with pytest.raises(ValueError, match="symlink"):
        validate_reference({"audio": "link.wav", "transcript": "ref.txt"}, base_dir=tmp_path)


def test_cache_signature_contains_model_and_reference_hash(tmp_path):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"fixture")
    config = {"engine": "qwen3-base", "voice": "f1-reference", "reference": {"audio": ref}}
    material = cache_signature_material("안녕하세요.", config)
    assert material["modelRevision"]
    assert material["referenceSha256"]["audio"]
    first = tts_cache_signature("안녕하세요.", config)
    ref.write_bytes(b"changed")
    assert tts_cache_signature("안녕하세요.", config) != first


def test_registry_rejects_duplicate_and_unknown_engine():
    class Fake:
        engine_id = "fake"

    register_engine("fake", lambda **_: Fake())
    assert "fake" in supported_engines()
    with pytest.raises(TtsEngineError, match="중복"):
        register_engine("fake", lambda **_: Fake())
    with pytest.raises(TtsEngineError, match="등록되지 않은"):
        create_engine("missing")


def test_runtime_tts_import_registers_builtin_engines():
    import brushvid.tts  # noqa: F401
    assert {"supertonic", "melo-ko", "qwen3-base"}.issubset(set(supported_engines()))


def test_manifest_schema_accepts_v1_and_new_engine_v2(tmp_path):
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    v1 = {"schemaVersion": 1, "engine": "supertonic", "projectId": "legacy"}
    assert list(validator.iter_errors(v1)) == []
    melo = {
        "schemaVersion": 2, "projectId": "melo", "engine": "melo-ko", "voice": "kr-default",
        "model": "myshell-ai/MeloTTS-Korean", "modelRevision": "a" * 40,
        "packageVersion": "0.1.2", "language": "ko", "nativeSampleRate": 44100,
        "outputSampleRate": 44100, "requestedSpeed": 1.0, "appliedSpeed": 1.0,
        "requestedTiming": "tts", "appliedTiming": "tts", "pauseMs": 300,
        "durationSec": 1.0, "sentenceCount": 1, "audioSha256": "b" * 64,
        "license": {"model": "MIT", "url": "https://example.com", "aiDisclosureRequired": True},
        "aiDisclosure": "AI 합성 음성", "speaker": "KR",
    }
    assert list(validator.iter_errors(melo)) == []
    melo["speaker"] = "fallback"
    assert list(validator.iter_errors(melo))


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("valid-supertonic-v1.json", True),
        ("valid-melo-v2.json", True),
        ("valid-qwen-v2.json", True),
        ("invalid-melo-speaker-v2.json", False),
    ],
)
def test_manifest_schema_fixtures(name, valid):
    data = json.loads((ROOT / "pipeline/tests/fixtures/tts-manifests" / name).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker()).iter_errors(data))
    assert bool(errors) is (not valid)


def test_melo_adapter_uses_pinned_files_and_kr_without_fallback(tmp_path):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "checkpoint.pth").write_bytes(b"checkpoint")
    calls = {}

    class Hps:
        class data:
            spk2id = {"KR": 7}
            sampling_rate = 44100

    class FakeModel:
        hps = Hps()

        def tts_to_file(self, text, speaker_id, **kwargs):
            calls.update(text=text, speaker_id=speaker_id, **kwargs)
            return np.zeros(441, dtype=np.float32)

    def factory(**kwargs):
        calls["factory"] = kwargs
        return FakeModel()

    adapter = MeloAdapter(model_dir=tmp_path, tts_factory=factory)
    result = adapter.synthesize("한국어.", voice="kr-default", language="ko", speed=1.1)
    assert result.sample_rate == 44100
    assert result.samples.shape == (441,)
    assert calls["factory"]["language"] == "KR"
    assert calls["factory"]["use_hf"] is False
    assert calls["speaker_id"] == 7
    assert calls["speed"] == pytest.approx(1.1)

    class BadModel(FakeModel):
        class hps:
            class data:
                spk2id = {"EN-US": 0}
                sampling_rate = 44100

    with pytest.raises(RuntimeError, match="fallback 금지"):
        MeloAdapter(model_dir=tmp_path, tts_factory=lambda **_: BadModel())


def test_qwen_worker_builds_reference_prompt_once_and_returns_ordered_relative_outputs(tmp_path):
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    audio = reference_dir / "ref.wav"
    transcript = reference_dir / "ref.txt"
    audio.write_bytes(b"reference")
    transcript.write_text("참조 문장입니다.", encoding="utf-8")
    output_dir = tmp_path / "work" / "request-1"
    calls = {"prompt": 0, "generate": 0}

    class FakeModel:
        def create_voice_clone_prompt(self, **kwargs):
            calls["prompt"] += 1
            assert kwargs["ref_audio"] == str(audio)
            assert kwargs["ref_text"] == "참조 문장입니다."
            return ["prompt"]

        def generate_voice_clone(self, **kwargs):
            calls["generate"] += 1
            assert kwargs["text"] == ["첫 문장.", "둘째 문장."]
            assert kwargs["language"] == ["Korean", "Korean"]
            return [np.zeros(240, dtype=np.float32), np.zeros(480, dtype=np.float32)], 24000

    request = {
        "protocolVersion": 1, "requestId": "request-1", "engine": "qwen3-base",
        "modelRevision": MODEL_REVISIONS["qwen3-base"], "language": "Korean",
        "xVectorOnlyMode": False,
        "reference": {
            "audio": "reference/ref.wav", "transcript": "reference/ref.txt",
            "audioSha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "transcriptSha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
        },
        "sentences": [{"id": "s1", "text": "첫 문장."}, {"id": "s2", "text": "둘째 문장."}],
        "outputDir": "work/request-1",
    }
    response = handle_request(FakeModel(), request, tmp_path)
    assert response["ok"] is True
    assert [item["id"] for item in response["outputs"]] == ["s1", "s2"]
    assert [item["filename"] for item in response["outputs"]] == ["sentence-0000.wav", "sentence-0001.wav"]
    assert calls == {"prompt": 1, "generate": 1}
    assert (output_dir / "sentence-0000.wav").is_file()


def test_qwen_worker_rejects_absolute_output_and_reference_hash_mismatch(tmp_path):
    request = {
        "protocolVersion": 1, "requestId": "bad", "engine": "qwen3-base",
        "modelRevision": MODEL_REVISIONS["qwen3-base"], "language": "Korean",
        "xVectorOnlyMode": False, "sentences": [{"id": "s1", "text": "문장."}],
        "reference": {"audio": "ref.wav", "transcript": "ref.txt", "audioSha256": "0" * 64, "transcriptSha256": "0" * 64},
        "outputDir": "/tmp/escape",
    }
    (tmp_path / "ref.wav").write_bytes(b"wav")
    (tmp_path / "ref.txt").write_text("text", encoding="utf-8")
    request["reference"]["audioSha256"] = hashlib.sha256(b"wav").hexdigest()
    request["reference"]["transcriptSha256"] = hashlib.sha256(b"text").hexdigest()
    with pytest.raises(WorkerProtocolError, match="outputDir"):
        handle_request(object(), request, tmp_path)
    request["outputDir"] = "work"
    request["reference"]["audioSha256"] = "0" * 64
    with pytest.raises(WorkerProtocolError, match="audioSha256"):
        handle_request(object(), request, tmp_path)


def test_qwen_worker_emits_json_only_for_malformed_json_and_missing_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "brushvid.tts_engines.qwen3_worker.resolve_local_snapshot",
        lambda *args, **kwargs: tmp_path,
    )
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("{malformed\n{\"command\":\"cancel\",\"requestId\":\"cancel-1\"}\n"))
    monkeypatch.setattr("sys.stdout", stdout)
    assert run_worker(
        model_dir=tmp_path,
        work_root=tmp_path / "work",
        model_loader=lambda *_: object(),
    ) == 0
    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert lines[0] == {"protocolVersion": 1, "ready": True, "modelRevision": MODEL_REVISIONS["qwen3-base"]}
    assert lines[1]["ok"] is False
    assert lines[1]["error"]["code"] == "PROTOCOL_ERROR"
    assert lines[2]["requestId"] == "cancel-1"
    assert lines[2]["error"]["code"] == "CANCELLED"

    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr(
        "brushvid.tts_engines.qwen3_worker.resolve_local_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("snapshot missing")),
    )
    assert run_worker(
        model_dir=tmp_path / "missing",
        work_root=tmp_path / "work-missing",
        model_loader=lambda *_: object(),
    ) == 1
    missing = json.loads(stdout.getvalue())
    assert missing["ready"] is False
    assert missing["error"]["code"] == "MODEL_MISSING"

    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr(
        "brushvid.tts_engines.qwen3_worker.resolve_local_snapshot",
        lambda *args, **kwargs: tmp_path,
    )
    assert run_worker(
        model_dir=tmp_path,
        work_root=tmp_path / "work-oom",
        model_loader=lambda *_: (_ for _ in ()).throw(MemoryError("out of memory")),
    ) == 1
    oom = json.loads(stdout.getvalue())
    assert oom["ready"] is False
    assert oom["error"]["code"] == "OOM"


class _FakeStdin:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, value):
        self.writes.append(value)

    def flush(self):
        return None

    def close(self):
        self.closed = True


class _FakeProcess:
    pid = 12345

    def __init__(self):
        self.stdin = _FakeStdin()
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls == 1 and timeout == 5:
            raise subprocess.TimeoutExpired("fake-qwen", timeout)
        return 0


def test_qwen_client_maps_timeout_protocol_error_and_kills_process_group(monkeypatch, tmp_path):
    client = QwenWorkerClient(
        python_path="python", model_dir=tmp_path, work_root=tmp_path / "work", device="cpu",
    )
    process = _FakeProcess()
    client.process = process
    killed = []
    monkeypatch.setattr(qwen_client.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    client._read_json = lambda timeout: (_ for _ in ()).throw(TimeoutError("slow"))
    with pytest.raises(TtsEngineError, match="GENERATION_TIMEOUT"):
        client.request({"requestId": "request-1"})
    assert killed == [(process.pid, signal.SIGKILL)]
    assert client.process is None

    client = QwenWorkerClient(
        python_path="python", model_dir=tmp_path, work_root=tmp_path / "work-2", device="cpu",
    )
    process = _FakeProcess()
    client.process = process
    client._read_json = lambda timeout: {"protocolVersion": 99, "requestId": "request-1", "ok": True}
    with pytest.raises(TtsEngineError, match="protocol version"):
        client.request({"requestId": "request-1"})

    client = QwenWorkerClient(
        python_path="python", model_dir=tmp_path, work_root=tmp_path / "work-3", device="cpu",
    )
    process = _FakeProcess()
    client.process = process
    client._read_json = lambda timeout: {"protocolVersion": 1, "requestId": "wrong", "ok": True}
    with pytest.raises(TtsEngineError, match="requestId"):
        client.request({"requestId": "request-1"})

    client = QwenWorkerClient(
        python_path="python", model_dir=tmp_path, work_root=tmp_path / "work-cancel", device="cpu",
    )
    process = _FakeProcess()
    client.process = process
    killed = []
    monkeypatch.setattr(qwen_client.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    client.cancel()
    assert killed == [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)]
    assert client.process is None

    client = QwenWorkerClient(
        python_path="python", model_dir=tmp_path, work_root=tmp_path / "work-4", device="cpu",
    )
    process = _FakeProcess()
    client._read_json = lambda timeout: (_ for _ in ()).throw(TimeoutError("startup slow"))
    monkeypatch.setattr(qwen_client.subprocess, "Popen", lambda *args, **kwargs: process)
    with pytest.raises(TtsEngineUnavailableError, match="STARTUP_TIMEOUT"):
        client.start()


def test_qwen_client_enforces_a_bounded_generation_timeout_with_real_selector(tmp_path):
    read_fd, write_fd = os.pipe()
    stdout = os.fdopen(read_fd, "r")
    client = QwenWorkerClient(
        python_path="python", model_dir=tmp_path, work_root=tmp_path / "work-timeout", device="cpu",
        generation_timeout=0.02,
    )
    process = _FakeProcess()
    process.stdout = stdout
    client.process = process
    try:
        with pytest.raises(TtsEngineError, match="GENERATION_TIMEOUT"):
            client.request({"requestId": "request-timeout"})
    finally:
        os.close(write_fd)
        stdout.close()


def test_qwen_adapter_cancel_cleans_controlled_reference_workspace(tmp_path, monkeypatch):
    audio = tmp_path / "ref.wav"
    transcript = tmp_path / "ref.txt"
    audio.write_bytes(b"reference")
    transcript.write_text("참조 문장", encoding="utf-8")
    monkeypatch.setattr(qwen_client, "resolve_local_snapshot", lambda *args, **kwargs: tmp_path)
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.work_root = kwargs["work_root"]

        def cancel(self):
            calls.append("cancel")

        def close(self):
            calls.append("close")

    adapter = QwenAdapter(
        reference={"audio": audio, "transcript": transcript},
        model_dir=tmp_path,
        worker_client_factory=FakeClient,
        work_root=tmp_path / "controlled",
    )
    workspace = adapter.work_root
    assert (workspace / "reference" / "reference.wav").is_file()
    adapter.cancel()
    assert calls == ["cancel"]
    assert not workspace.exists()

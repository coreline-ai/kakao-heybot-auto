from __future__ import annotations

import stat
from pathlib import Path

import pytest

from brushvid.stable_audio import StableAudioError, generate, preflight


WEIGHTS = (
    "models/mlx/dit_sm-music_f16.npz",
    "models/mlx/same_s_decoder_f32.npz",
    "models/mlx/same_s_encoder_f32.npz",
    "models/mlx/t5gemma_f16.npz",
)


def make_runtime(tmp_path: Path) -> Path:
    root = tmp_path / "mlx"
    (root / "models/mlx").mkdir(parents=True)
    for relative in WEIGHTS:
        path = root / relative
        path.write_bytes(b"fake")
    wrapper = root / "sa3"
    wrapper.write_text(
        """#!/usr/bin/env python3
import os
import struct
import sys
import time
import wave

args = sys.argv[1:]
def value(name):
    return args[args.index(name) + 1]

mode = os.environ.get("FAKE_SA3_MODE", "ok")
if mode == "fail":
    print("fake failure", file=sys.stderr)
    raise SystemExit(7)
if mode == "timeout":
    time.sleep(2)
out = value("--out")
seconds = float(value("--seconds"))
frames = int(seconds * 44100)
with wave.open(out, "wb") as stream:
    stream.setnchannels(2)
    stream.setsampwidth(2)
    stream.setframerate(44100)
    stream.writeframes(struct.pack("<hh", 0, 0) * frames)
""",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return root


def request(**overrides):
    value = {
        "projectId": "fake-stable",
        "durationSec": 15,
        "seed": 42,
        "preset": "cinematic-piano",
        "prompt": "epic piano",
        "negativePrompt": "vocals",
        "cfg": 2.5,
        "steps": 8,
    }
    value.update(overrides)
    return value


def test_preflight_requires_wrapper_and_all_weights(tmp_path: Path):
    root = make_runtime(tmp_path)
    result = preflight(root)
    assert result["bundle"] == "sm-music"
    assert result["decoder"] == "same-s"
    assert len(result["requiredWeights"]) == 4

    (root / WEIGHTS[0]).unlink()
    with pytest.raises(StableAudioError, match="가중치"):
        preflight(root)


def test_generate_uses_safe_argv_and_records_audio_metadata(tmp_path: Path):
    root = make_runtime(tmp_path)
    output = tmp_path / "candidate.wav"
    result = generate(request(), output, root=root)
    assert output.is_file()
    assert result["audio"] == {
        "codec": "pcm_s16le",
        "sampleRateHz": 44100,
        "bitDepth": 16,
        "channels": 2,
        "frames": 661500,
        "durationSec": 15.0,
    }
    assert result["parameters"]["prompt"] == "epic piano"
    assert result["command"][0] == str(root / "sa3")
    assert result["sha256"]


@pytest.mark.parametrize(
    ("mode", "pattern"),
    (("fail", "생성 실패"), ("timeout", "timeout")),
)
def test_generate_surfaces_subprocess_failures_and_cleans_partial_output(tmp_path: Path, monkeypatch, mode: str, pattern: str):
    root = make_runtime(tmp_path)
    output = tmp_path / "candidate.wav"
    monkeypatch.setenv("FAKE_SA3_MODE", mode)
    timeout = 0.05 if mode == "timeout" else None
    with pytest.raises(StableAudioError, match=pattern):
        generate(request(), output, root=root, timeout_sec=timeout)
    assert not output.exists()
    assert not list(tmp_path.glob(".stable-audio-*.wav"))


def test_generate_rejects_existing_output_without_force(tmp_path: Path):
    root = make_runtime(tmp_path)
    output = tmp_path / "candidate.wav"
    output.write_bytes(b"keep")
    with pytest.raises(StableAudioError, match="--force"):
        generate(request(), output, root=root)
    assert output.read_bytes() == b"keep"


@pytest.mark.parametrize("field", ("durationSec", "cfg"))
def test_generate_rejects_non_finite_sampling_values(tmp_path: Path, field: str):
    root = make_runtime(tmp_path)
    values = request(**{field: float("nan")})
    with pytest.raises(StableAudioError, match="durationSec|cfg"):
        generate(values, tmp_path / "nan.wav", root=root)


def test_generate_rejects_wrong_wav_format(tmp_path: Path):
    root = make_runtime(tmp_path)
    wrapper = root / "sa3"
    wrapper.write_text(
        """#!/usr/bin/env python3
import sys
from pathlib import Path
Path(sys.argv[sys.argv.index('--out') + 1]).write_bytes(b'not wav')
""",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    with pytest.raises(StableAudioError, match="유효한 WAV"):
        generate(request(), tmp_path / "bad.wav", root=root)

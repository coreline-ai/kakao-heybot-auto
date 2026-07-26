from __future__ import annotations

import argparse
import importlib.util
import json
import stat
import subprocess
import sys
from pathlib import Path

from brushvid.piano_bgm import normalize_request

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "bin" / "piano-bgm.py"


def load_cli_module():
    spec = importlib.util.spec_from_file_location("piano_bgm_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def fake_stable_runtime(tmp_path: Path) -> Path:
    root = tmp_path / "stable-audio-3"
    (root / "models/mlx").mkdir(parents=True)
    for name in ("dit_sm-music_f16.npz", "same_s_decoder_f32.npz", "same_s_encoder_f32.npz", "t5gemma_f16.npz"):
        (root / "models/mlx" / name).write_bytes(b"fake")
    wrapper = root / "sa3"
    wrapper.write_text(
        """#!/usr/bin/env python3
import math
import struct
import sys
import wave

args = sys.argv[1:]
def value(name):
    return args[args.index(name) + 1]

seconds = float(value('--seconds'))
frames = int(seconds * 44100)
with wave.open(value('--out'), 'wb') as stream:
    stream.setnchannels(2)
    stream.setsampwidth(2)
    stream.setframerate(44100)
    samples = []
    for index in range(frames):
        sample = int(5000 * math.sin(2 * math.pi * 261.63 * index / 44100))
        samples.append(struct.pack('<hh', sample, sample))
    stream.writeframes(b''.join(samples))
""",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return root


def test_cli_validate_and_compose_without_renderer(tmp_path: Path):
    request = tmp_path / "request.yaml"
    request.write_text("""projectId: cli-fantasy\nkind: piano-bgm\ndurationSec: 30\npreset: fantasy-piano\nkey: D-lydian\nseed: 12\n""", encoding="utf-8")
    projects, output = tmp_path / "projects", tmp_path / "output"
    command = [sys.executable, str(CLI), "--projects-root", str(projects), "--output-root", str(output)]
    checked = subprocess.run(command + ["validate", "--request", str(request)], check=True, capture_output=True, text=True)
    assert json.loads(checked.stdout)["request"]["key"] == "D-lydian"
    composed = subprocess.run(command + ["compose", "--request", str(request)], check=True, capture_output=True, text=True)
    data = json.loads(composed.stdout)
    assert data["lint"]["status"] == "PASS"
    assert (projects / "cli-fantasy" / "score.json").is_file()


def test_cli_stable_audio_build_creates_candidate_gate(tmp_path: Path):
    request = tmp_path / "request.yaml"
    request.write_text("""projectId: cli-stable
kind: piano-bgm
durationSec: 15
preset: cinematic-piano
purpose: featured
engine: stable-audio-3-mlx
seed: 12
cfg: 1
steps: 1
""", encoding="utf-8")
    projects, output, runtime = tmp_path / "projects", tmp_path / "output", fake_stable_runtime(tmp_path)
    command = [sys.executable, str(CLI), "--projects-root", str(projects), "--output-root", str(output),
               "--stable-audio-root", str(runtime)]
    built = subprocess.run(command + ["build", "--request", str(request)], check=True, capture_output=True, text=True)
    data = json.loads(built.stdout)
    candidate = output / "cli-stable"
    assert data["engine"]["engine"] == "stable-audio-3-mlx"
    assert data["qa"]["status"] == "PENDING_USER_LISTENING"
    assert all((candidate / name).is_file() for name in (
        "source.wav", "generation.json", "raw-48k24.wav", "master-48k24.wav",
        "cli-stable-44k16.wav", "provenance.json", "qa.json", "generated-bgm-manifest.json",
    ))
    manifest = json.loads((candidate / "generated-bgm-manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"] == "stable-audio-3-mlx"
    assert manifest["status"] == "PENDING_USER_LISTENING"


def test_cli_stable_audio_is_explicitly_not_silent_fallback(tmp_path: Path):
    request = tmp_path / "request.yaml"
    request.write_text("""projectId: cli-no-runtime
kind: piano-bgm
durationSec: 15
preset: cinematic-piano
engine: stable-audio-3-mlx
""", encoding="utf-8")
    result = subprocess.run([sys.executable, str(CLI), "--projects-root", str(tmp_path / "projects"),
                             "--output-root", str(tmp_path / "output"), "build", "--request", str(request)],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "Stable Audio" in result.stderr


def test_cli_auto_records_stable_audio_unavailable_fallback(tmp_path: Path):
    request = normalize_request({"projectId": "cli-auto-fallback", "kind": "piano-bgm", "durationSec": 15,
                                 "preset": "cinematic-piano", "purpose": "featured", "engine": "auto", "seed": 12})
    decision = load_cli_module()._runtime_engine(
        argparse.Namespace(stable_audio_root=str(tmp_path / "missing-runtime")), request)
    assert decision["engine"] == "sample-score"
    assert decision["reason"].startswith("auto-fallback-stable-audio-unavailable:")

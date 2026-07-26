"""Tests for bin/seamless-short.py — real shipped module path (no Remotion)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bin" / "seamless-short.py"
SPEC = importlib.util.spec_from_file_location("seamless_short_cli", SCRIPT)
assert SPEC and SPEC.loader
ss = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ss)


def _rgb(h: int, w: int, color: tuple[int, int, int]) -> np.ndarray:
    im = np.zeros((h, w, 3), dtype=np.float32)
    im[:, :] = color
    return im


def test_sha256_file_changes_when_bytes_change(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"handoff-bytes-v1")
    b.write_bytes(b"handoff-bytes-v1")
    assert ss.sha256_file(a) == ss.sha256_file(b)
    b.write_bytes(b"handoff-bytes-v2")
    assert ss.sha256_file(a) != ss.sha256_file(b)


def test_evaluate_frame0_gate_pass_identical_images():
    start = _rgb(64, 48, (120, 130, 140))
    frame0 = start.copy()
    result = ss.evaluate_frame0_gate(start, frame0)
    assert result["pass"] is True
    assert result["errors"] == []
    assert result["metrics"]["dmeanY"] < 0.01
    assert result["metrics"]["mae"] < 0.01


def test_evaluate_frame0_gate_fail_large_brightness_jump():
    start = _rgb(64, 48, (40, 40, 40))
    frame0 = _rgb(64, 48, (200, 200, 200))
    result = ss.evaluate_frame0_gate(start, frame0)
    assert result["pass"] is False
    assert any("ΔmeanY" in e or "MAE" in e for e in result["errors"])
    assert result["metrics"]["dmeanY"] > ss.DEFAULT_FRAME0_MAX_DMEAN_Y


def test_frame_pair_join_score_prefers_closer_match():
    """join-score ranking: lower score for near-identical frames."""
    base = _rgb(40, 30, (100, 110, 120))
    close = base + 2.0
    far = _rgb(40, 30, (20, 200, 30))
    m_close = ss._frame_pair_metrics(base, close)
    m_far = ss._frame_pair_metrics(base, far)
    assert m_close["score"] < m_far["score"]
    assert m_close["mae"] < m_far["mae"]


def test_init_creates_project_yaml(tmp_path: Path):
    proj = tmp_path / "seamless-test-init"
    ss.main(
        [
            "init",
            "--project-dir",
            str(proj),
            "--project-id",
            "seamless-test-init",
            "--scenes",
            "2",
            "--scene-seconds",
            "10",
            "--head-trim",
            "2",
        ]
    )
    ypath = proj / "project.yaml"
    assert ypath.is_file()
    data = ss.load_yaml(ypath)
    assert data["project"]["scene_count"] == 2
    assert data["project"]["total_duration"] == 18.0
    assert data["assembly"]["mode"] == "head_trim"
    assert data["assembly"]["head_trim_sec"] == 2.0
    assert (proj / "scenes" / "scene_01").is_dir()
    assert (proj / "scenes" / "scene_02").is_dir()


def test_frame0_check_cli_pass_and_fail_with_tiny_mp4(tmp_path: Path):
    """Drive real CLI entry: frame0-check exit 0 vs 1 on synthetic project."""
    proj = tmp_path / "f0proj"
    ss.main(
        [
            "init",
            "--project-dir",
            str(proj),
            "--project-id",
            "f0proj",
            "--scenes",
            "1",
            "--scene-seconds",
            "1",
        ]
    )
    scene = proj / "scenes" / "scene_01"
    # Matching start + 1-frame-ish video from same color
    start = scene / "start_image.png"
    Image.fromarray(np.full((48, 32, 3), 90, dtype=np.uint8)).save(start)
    video = scene / "scene_01.mp4"
    # ffmpeg solid color short clip matching start
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x5a5a5a:s=32x48:d=0.2",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    r_pass = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "frame0-check",
            "--project-dir",
            str(proj),
            "--scene",
            "1",
        ],
        capture_output=True,
        text=True,
    )
    assert r_pass.returncode == 0, r_pass.stdout + r_pass.stderr
    assert "FRAME0 PASS" in r_pass.stdout
    qa = json.loads((scene / "qa.json").read_text(encoding="utf-8"))
    assert qa["hard"]["frame0"]["pass"] is True

    # Bright mismatch video → fail
    video_bad = scene / "scene_01_bad.mp4"
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xffffff:s=32x48:d=0.2",
            "-pix_fmt",
            "yuv420p",
            str(video_bad),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    r_fail = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "frame0-check",
            "--project-dir",
            str(proj),
            "--scene",
            "1",
            "--video",
            str(video_bad),
        ],
        capture_output=True,
        text=True,
    )
    assert r_fail.returncode != 0, r_fail.stdout + r_fail.stderr
    assert "FRAME0 FAIL" in r_fail.stdout


def test_handoff_sha_chain_wiring(tmp_path: Path):
    """handoff copies last usable frame bytes to next start (sha chain)."""
    proj = tmp_path / "sha_chain"
    ss.main(
        [
            "init",
            "--project-dir",
            str(proj),
            "--project-id",
            "sha_chain",
            "--scenes",
            "2",
            "--scene-seconds",
            "1",
        ]
    )
    s1 = proj / "scenes" / "scene_01"
    # Distinct patterned frame as video content
    frame = Image.fromarray(
        np.stack(
            [
                np.linspace(0, 255, 64, dtype=np.uint8)[None, :].repeat(48, 0),
                np.full((48, 64), 80, dtype=np.uint8),
                np.full((48, 64), 160, dtype=np.uint8),
            ],
            axis=-1,
        )
    )
    png = s1 / "src.png"
    frame.save(png)
    video = s1 / "scene_01.mp4"
    subprocess.check_call(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(png),
            "-t",
            "0.3",
            "-r",
            "10",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ss.main(["handoff", "--project-dir", str(proj), "--scene", "1"])
    handoff = s1 / "handoff_frame.png"
    next_start = proj / "scenes" / "scene_02" / "start_image.png"
    assert handoff.is_file()
    assert next_start.is_file()
    assert ss.sha256_file(handoff) == ss.sha256_file(next_start)

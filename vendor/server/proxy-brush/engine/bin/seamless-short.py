#!/usr/bin/env python3
"""Seamless Short Video — Last Frame Handoff helpers.

Subcommands:
  init          Create project skeleton + project.yaml
  handoff       Extract Last Usable Frame from a scene video and wire next start image
  verify        Hard-gate checks (start hash chain, duration, blur)
  concat        Match-cut concat of scene mp4 files
  join-score    Score candidate head_trim join points (s1 end vs s2@t)
  frame0-check  Compare start_image vs video frame0 (ΔY / MAE hard gate)

This CLI does not call Remotion or bin/build.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

try:
    from PIL import Image
    import numpy as np
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    np = None  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[1]


def die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        die("PyYAML 필요 — pipeline/.venv 또는 pip install pyyaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        die(f"YAML 객체 아님: {path}")
    return data


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    if yaml is None:
        die("PyYAML 필요")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scene_dir(project_dir: Path, index: int) -> Path:
    return project_dir / "scenes" / f"scene_{index:02d}"


def ffprobe_json(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration,avg_frame_rate:format=duration",
        "-of", "json",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)


def video_duration_sec(path: Path) -> float:
    meta = ffprobe_json(path)
    fmt = meta.get("format") or {}
    if fmt.get("duration"):
        return float(fmt["duration"])
    streams = meta.get("streams") or []
    if streams and streams[0].get("duration"):
        return float(streams[0]["duration"])
    die(f"duration 없음: {path}")


def video_size(path: Path) -> tuple[int, int]:
    meta = ffprobe_json(path)
    streams = meta.get("streams") or []
    if not streams:
        die(f"video stream 없음: {path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def blur_score(image_path: Path) -> float:
    """Laplacian variance — higher = sharper."""
    if Image is None or np is None:
        return 999.0  # skip gate if deps missing
    img = Image.open(image_path).convert("L")
    arr = np.asarray(img, dtype=np.float64)
    # 3x3 Laplacian kernel
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    # pad and convolve manually for no scipy dependency
    p = np.pad(arr, 1, mode="edge")
    lap = (
        kernel[0, 1] * p[0:-2, 1:-1]
        + kernel[1, 0] * p[1:-1, 0:-2]
        + kernel[1, 1] * p[1:-1, 1:-1]
        + kernel[1, 2] * p[1:-1, 2:]
        + kernel[2, 1] * p[2:, 1:-1]
    )
    return float(lap.var())


def extract_frame_at(video: Path, time_sec: float, out_png: Path) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    t = max(0.0, time_sec)
    # Input-seek after -i is slower but more reliable near EOF.
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-ss", f"{t:.4f}",
        "-frames:v", "1",
        "-update", "1",
        str(out_png),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out_png.is_file() or out_png.stat().st_size < 32:
        raise RuntimeError(f"frame not written at t={t}: {out_png}")


def pick_last_usable_frame(
    video: Path,
    out_png: Path,
    lookback_sec: float = 0.8,
    sample_fps: float = 10.0,
    min_blur: float = 40.0,
) -> dict[str, Any]:
    duration = video_duration_sec(video)
    # Stay ≥2 frames away from EOF to avoid empty extracts on short VFR tails.
    end_cap = max(0.0, duration - 0.05)
    start = max(0.0, end_cap - lookback_sec)
    n = max(1, int(math.ceil(lookback_sec * sample_fps)))
    step = (end_cap - start) / max(n - 1, 1) if n > 1 else 0.0
    times = [start + i * step for i in range(n)]
    times.append(end_cap)
    times = sorted(set(round(t, 4) for t in times if t >= 0))

    with tempfile.TemporaryDirectory(prefix="seamless-handoff-") as tmp:
        tmp_path = Path(tmp)
        candidates: list[tuple[float, float, Path]] = []
        for i, t in enumerate(times):
            p = tmp_path / f"c_{i:03d}.png"
            try:
                extract_frame_at(video, t, p)
            except (subprocess.CalledProcessError, RuntimeError):
                continue
            if not p.is_file():
                continue
            score = blur_score(p)
            candidates.append((t, score, p))

        if not candidates:
            die(f"프레임 추출 실패: {video}")

        # prefer latest frame among those meeting min_blur; else best blur overall
        sharp = [c for c in candidates if c[1] >= min_blur]
        if sharp:
            chosen = max(sharp, key=lambda c: c[0])
            warning = None
        else:
            chosen = max(candidates, key=lambda c: c[1])
            warning = (
                f"all candidates below min_blur={min_blur}; "
                f"picked sharpest at t={chosen[0]:.3f}s score={chosen[1]:.1f}"
            )

        out_png.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(chosen[2], out_png)
        return {
            "time_sec": chosen[0],
            "blur_score": chosen[1],
            "duration_sec": duration,
            "warning": warning,
            "candidates": [{"t": t, "blur": s} for t, s, _ in candidates],
        }


def cmd_init(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).resolve()
    project_id = args.project_id or project_dir.name
    scenes = int(args.scenes)
    scene_seconds = float(args.scene_seconds)
    if scenes < 1 or scenes > 18:
        die("scenes must be 1..18")

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "character").mkdir(exist_ok=True)
    (project_dir / "story").mkdir(exist_ok=True)
    (project_dir / "edit").mkdir(exist_ok=True)
    (project_dir / "report").mkdir(exist_ok=True)

    scene_entries = []
    for i in range(1, scenes + 1):
        d = scene_dir(project_dir, i)
        d.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "id": f"scene_{i:02d}",
            "start_image": f"scenes/scene_{i:02d}/start_image.png",
            "source_video": f"scenes/scene_{i:02d}/scene_{i:02d}.mp4",
            "handoff_frame": f"scenes/scene_{i:02d}/handoff_frame.png",
            "qa_status": "pending",
        }
        if i >= 2:
            entry["start_image_source"] = f"scenes/scene_{i-1:02d}/handoff_frame.png"
        scene_entries.append(entry)

    head_trim = float(getattr(args, "head_trim", 0) or 0)
    # doc16 A′: scene1 full + each later scene (gen - head_trim)
    if head_trim > 0 and scenes >= 1:
        final_duration = scene_seconds + max(0, scenes - 1) * max(0.0, scene_seconds - head_trim)
        transition_mode = "last_frame_match_cut_head_trim"
    else:
        final_duration = scenes * scene_seconds
        transition_mode = args.transition_mode

    data: dict[str, Any] = {
        "project": {
            "id": project_id,
            "name": args.title or project_id,
            "total_duration": final_duration,
            "scene_count": scenes,
            "scene_duration": scene_seconds,
            "aspect_ratio": args.aspect,
            "transition_mode": transition_mode,
            "dialogue": False,
            "status": "PROJECT_INITIALIZED",
        },
        "generator": {
            "id": args.generator,
            "max_duration_sec": scene_seconds,
            "aspect": args.aspect,
            "supports_exact_start_frame": "partial",
        },
        "character_lock": {
            "path": "character/character_lock.md",
            "reference": "character/character_reference.png",
        },
        "scenes": scene_entries,
        "retry": {"max_per_scene": 3, "immutable_completed": True},
    }
    if head_trim > 0:
        data["assembly"] = {
            "mode": "head_trim",
            "head_trim_sec": head_trim,
            "unique_content_sec": max(0.0, scene_seconds - head_trim),
            "final_duration_sec": final_duration,
            "note": "doc16 A′: gen full clips; discard first head_trim of scenes 2..N",
        }
        data["content"] = {
            "unique_sec": max(0.0, scene_seconds - head_trim),
            "bridge_sec": head_trim,
            "script_sec": max(0.0, scene_seconds - head_trim - 0.5),
        }
    dump_yaml(project_dir / "project.yaml", data)

    lock = project_dir / "character" / "character_lock.md"
    if not lock.exists():
        lock.write_text(
            f"# Character Lock — {project_id}\n\n"
            "- name:\n- species/type:\n- face:\n- outfit:\n- body_ratio:\n"
            "- fixed_prop:\n- style:\n- locked: face structure, colors, outfit, prop, style\n"
            "- mutable: expression, pose, gaze, location interaction\n",
            encoding="utf-8",
        )
    summary = project_dir / "story" / "story_summary.md"
    if not summary.exists():
        summary.write_text(f"# {args.title or project_id}\n\n(story summary)\n", encoding="utf-8")
    plan = project_dir / "story" / "scene_plan.md"
    if not plan.exists():
        lines = [f"# Scene plan — {scenes} × {scene_seconds:g}s\n"]
        for i in range(1, scenes + 1):
            lines.append(f"## scene_{i:02d}\n- role:\n- start:\n- action:\n- end:\n- link_motion:\n")
        plan.write_text("\n".join(lines), encoding="utf-8")

    print(f"initialized {project_dir}")
    print(
        f"  project.yaml  scenes={scenes} gen={scenes * scene_seconds:g}s "
        f"final={final_duration:g}s aspect={args.aspect}"
        + (f" head_trim={head_trim:g}s" if head_trim > 0 else "")
    )


def cmd_handoff(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).resolve()
    idx = int(args.scene)
    proj = load_yaml(project_dir / "project.yaml")
    scene_count = int(proj["project"]["scene_count"])
    if idx < 1 or idx > scene_count:
        die(f"scene {idx} out of range 1..{scene_count}")

    d = scene_dir(project_dir, idx)
    video = Path(args.video).resolve() if args.video else d / f"scene_{idx:02d}.mp4"
    if not video.is_file():
        die(f"video not found: {video}")

    # normalize name inside scene folder
    dest_video = d / f"scene_{idx:02d}.mp4"
    if video.resolve() != dest_video.resolve():
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(video, dest_video)
        video = dest_video

    handoff = d / "handoff_frame.png"
    result = pick_last_usable_frame(
        video,
        handoff,
        lookback_sec=float(args.lookback),
        sample_fps=float(args.sample_fps),
        min_blur=float(args.min_blur),
    )

    # wire next scene start image
    if idx < scene_count:
        next_d = scene_dir(project_dir, idx + 1)
        next_d.mkdir(parents=True, exist_ok=True)
        next_start = next_d / "start_image.png"
        shutil.copy2(handoff, next_start)
        print(f"wired next start: {next_start.relative_to(project_dir)}")

    # update project.yaml scene status
    for sc in proj.get("scenes") or []:
        if sc.get("id") == f"scene_{idx:02d}":
            sc["qa_status"] = "handoff_ready"
            sc["handoff_meta"] = {
                "time_sec": result["time_sec"],
                "blur_score": round(result["blur_score"], 2),
                "duration_sec": round(result["duration_sec"], 3),
                "warning": result["warning"],
            }
    dump_yaml(project_dir / "project.yaml", proj)

    meta_path = d / "handoff_meta.json"
    meta_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"handoff: {handoff}")
    print(f"  t={result['time_sec']:.3f}s blur={result['blur_score']:.1f} dur={result['duration_sec']:.3f}s")
    if result["warning"]:
        print(f"  warning: {result['warning']}")


def cmd_verify(args: argparse.Namespace) -> None:
    project_dir = Path(args.project_dir).resolve()
    proj = load_yaml(project_dir / "project.yaml")
    scene_seconds = float(proj["project"]["scene_duration"])
    tol = float(args.duration_tol)
    min_blur = float(args.min_blur)
    errors: list[str] = []
    warnings: list[str] = []

    scenes = proj.get("scenes") or []
    for i, sc in enumerate(scenes, start=1):
        d = scene_dir(project_dir, i)
        sid = sc.get("id", f"scene_{i:02d}")
        start = d / "start_image.png"
        video = d / f"scene_{i:02d}.mp4"
        handoff = d / "handoff_frame.png"

        if i >= 2:
            prev_handoff = scene_dir(project_dir, i - 1) / "handoff_frame.png"
            if not start.is_file():
                errors.append(f"{sid}: missing start_image.png")
            elif not prev_handoff.is_file():
                errors.append(f"{sid}: missing prev handoff for hash check")
            else:
                if sha256_file(start) != sha256_file(prev_handoff):
                    errors.append(f"{sid}: start_image sha256 != prev handoff_frame")

        if video.is_file():
            dur = video_duration_sec(video)
            if abs(dur - scene_seconds) > tol:
                errors.append(f"{sid}: duration {dur:.3f}s not in {scene_seconds}±{tol}")
            w, h = video_size(video)
            sc["_probed"] = {"duration": dur, "width": w, "height": h}
        else:
            warnings.append(f"{sid}: video missing (skip duration)")

        if handoff.is_file():
            score = blur_score(handoff)
            if score < min_blur:
                errors.append(f"{sid}: handoff blur {score:.1f} < {min_blur}")
            sc.setdefault("handoff_meta", {})["blur_score_verify"] = round(score, 2)
        else:
            warnings.append(f"{sid}: handoff missing")

    report = {
        "project": proj["project"]["id"],
        "errors": errors,
        "warnings": warnings,
        "pass": len(errors) == 0,
    }
    report_path = project_dir / "report" / "verify-hard-gate.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"FAIL  {e}")
    if errors:
        print(f"VERIFY FAIL ({len(errors)} errors) → {report_path}")
        raise SystemExit(1)
    print(f"VERIFY PASS → {report_path}")


def _load_rgb(path: Path) -> Any:
    if Image is None or np is None:
        die("Pillow + numpy 필요 — join-score/auto-head-trim")
    return np.array(Image.open(path).convert("RGB"), dtype=np.float32)


def _mean_y(im: Any) -> float:
    return float((0.299 * im[:, :, 0] + 0.587 * im[:, :, 1] + 0.114 * im[:, :, 2]).mean())


def _luma(im: Any) -> Any:
    return 0.299 * im[:, :, 0] + 0.587 * im[:, :, 1] + 0.114 * im[:, :, 2]


def _resize_to_match(a: Any, b: Any) -> tuple[Any, Any]:
    """Return (a, b) with b resized to a's HxW if needed."""
    if a.shape == b.shape:
        return a, b
    b_img = Image.fromarray(b.astype(np.uint8)).resize(
        (a.shape[1], a.shape[0]), Image.BILINEAR
    )
    return a, np.array(b_img, dtype=np.float32)


def _frame_pair_metrics(a: Any, b: Any) -> dict[str, float]:
    a, b = _resize_to_match(a, b)
    d = a - b
    mae = float(np.abs(d).mean())
    rmse = float(np.sqrt((d ** 2).mean()))
    h, w = a.shape[:2]
    y0, y1 = int(h * 0.25), int(h * 0.85)
    x0, x1 = int(w * 0.15), int(w * 0.85)
    mae_c = float(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1]).mean())
    ga = _luma(a)
    gb = _luma(b)
    ga0, gb0 = ga - ga.mean(), gb - gb.mean()
    denom = float(np.sqrt((ga0 ** 2).sum()) * np.sqrt((gb0 ** 2).sum()) + 1e-6)
    corr = float((ga0 * gb0).sum() / denom)
    ya, yb = _mean_y(a), _mean_y(b)
    # Lower score is better. Prefer low MAE/center MAE, high corr, low dY.
    score = mae + 0.5 * mae_c + 2.0 * abs(ya - yb) + 40.0 * max(0.0, 1.0 - corr)
    return {
        "mae": mae,
        "rmse": rmse,
        "mae_center": mae_c,
        "corr": corr,
        "meanY_a": ya,
        "meanY_b": yb,
        "dY": abs(ya - yb),
        "score": score,
    }


# Defaults aligned with docs/seamless-short-video/15 C12 (and 13 C-EXP hard).
DEFAULT_FRAME0_MAX_DMEAN_Y = 3.5
DEFAULT_FRAME0_MAX_DBOTTOM_Y = 4.0
DEFAULT_FRAME0_MAX_DCENTER_Y = 4.0
DEFAULT_FRAME0_MAX_MAE = 8.0


def evaluate_frame0_gate(
    start_im: Any,
    frame0_im: Any,
    *,
    max_dmean_y: float = DEFAULT_FRAME0_MAX_DMEAN_Y,
    max_dbottom_y: float = DEFAULT_FRAME0_MAX_DBOTTOM_Y,
    max_dcenter_y: float = DEFAULT_FRAME0_MAX_DCENTER_Y,
    max_mae: float = DEFAULT_FRAME0_MAX_MAE,
) -> dict[str, Any]:
    """Compare start image vs decoded frame0; return metrics + pass + errors.

    Pure metrics on RGB float arrays — callable from CLI and unit tests.
    """
    if Image is None or np is None:
        die("Pillow + numpy 필요 — frame0-check")
    start_im, frame0_im = _resize_to_match(start_im, frame0_im)
    pair = _frame_pair_metrics(start_im, frame0_im)
    ys = _luma(start_im)
    yf = _luma(frame0_im)
    h, w = ys.shape
    # bottom band ~ lower 20%
    bot_s = float(ys[int(h * 0.80) :, :].mean())
    bot_f = float(yf[int(h * 0.80) :, :].mean())
    # center crop ~ middle 50%
    c0, c1 = int(h * 0.25), int(h * 0.75)
    x0, x1 = int(w * 0.25), int(w * 0.75)
    cen_s = float(ys[c0:c1, x0:x1].mean())
    cen_f = float(yf[c0:c1, x0:x1].mean())
    dmean = abs(pair["meanY_a"] - pair["meanY_b"])
    dbottom = abs(bot_s - bot_f)
    dcenter = abs(cen_s - cen_f)
    mae = pair["mae"]
    thresholds = {
        "max_dmean_y": max_dmean_y,
        "max_dbottom_y": max_dbottom_y,
        "max_dcenter_y": max_dcenter_y,
        "max_mae": max_mae,
    }
    errors: list[str] = []
    if dmean > max_dmean_y:
        errors.append(f"|ΔmeanY|={dmean:.3f} > {max_dmean_y}")
    if dbottom > max_dbottom_y:
        errors.append(f"|ΔbottomY|={dbottom:.3f} > {max_dbottom_y}")
    if dcenter > max_dcenter_y:
        errors.append(f"|ΔcenterY|={dcenter:.3f} > {max_dcenter_y}")
    if mae > max_mae:
        errors.append(f"MAE={mae:.3f} > {max_mae}")
    return {
        "pass": len(errors) == 0,
        "errors": errors,
        "metrics": {
            "meanY_start": pair["meanY_a"],
            "meanY_frame0": pair["meanY_b"],
            "dmeanY": dmean,
            "bottomY_start": bot_s,
            "bottomY_frame0": bot_f,
            "dbottomY": dbottom,
            "centerY_start": cen_s,
            "centerY_frame0": cen_f,
            "dcenterY": dcenter,
            "mae": mae,
            "mae_center": pair["mae_center"],
            "corr": pair["corr"],
        },
        "thresholds": thresholds,
    }


def frame0_check_paths(
    start_image: Path,
    video: Path,
    *,
    max_dmean_y: float = DEFAULT_FRAME0_MAX_DMEAN_Y,
    max_dbottom_y: float = DEFAULT_FRAME0_MAX_DBOTTOM_Y,
    max_dcenter_y: float = DEFAULT_FRAME0_MAX_DCENTER_Y,
    max_mae: float = DEFAULT_FRAME0_MAX_MAE,
) -> dict[str, Any]:
    """Extract video frame0 and evaluate against start_image (shipped path)."""
    if not start_image.is_file():
        die(f"start image missing: {start_image}")
    if not video.is_file():
        die(f"video missing: {video}")
    with tempfile.TemporaryDirectory(prefix="seamless-f0-") as tmp:
        f0 = Path(tmp) / "frame0.png"
        extract_frame_at(video, 0.0, f0)
        result = evaluate_frame0_gate(
            _load_rgb(start_image),
            _load_rgb(f0),
            max_dmean_y=max_dmean_y,
            max_dbottom_y=max_dbottom_y,
            max_dcenter_y=max_dcenter_y,
            max_mae=max_mae,
        )
    result["start_image"] = str(start_image)
    result["video"] = str(video)
    return result


def cmd_frame0_check(args: argparse.Namespace) -> None:
    """Hard-gate: start_image vs scene video frame0 (doc15 C12)."""
    project_dir = Path(args.project_dir).resolve()
    idx = int(args.scene)
    d = scene_dir(project_dir, idx)
    start = Path(args.start_image).resolve() if args.start_image else d / "start_image.png"
    video = Path(args.video).resolve() if args.video else d / f"scene_{idx:02d}.mp4"

    result = frame0_check_paths(
        start,
        video,
        max_dmean_y=float(args.max_dmean_y),
        max_dbottom_y=float(args.max_dbottom_y),
        max_dcenter_y=float(args.max_dcenter_y),
        max_mae=float(args.max_mae),
    )
    result["scene"] = f"scene_{idx:02d}"
    result["project_dir"] = str(project_dir)

    # Merge into scenes/scene_XX/qa.json
    qa_path = d / "qa.json"
    qa: dict[str, Any] = {}
    if qa_path.is_file():
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            if not isinstance(qa, dict):
                qa = {}
        except json.JSONDecodeError:
            qa = {}
    hard = qa.setdefault("hard", {})
    hard["frame0"] = {
        "pass": result["pass"],
        "metrics": result["metrics"],
        "thresholds": result["thresholds"],
        "errors": result["errors"],
    }
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps(qa, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = project_dir / "report" / f"frame0-check-scene_{idx:02d}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    m = result["metrics"]
    print(
        f"frame0-check scene_{idx:02d}: "
        f"dmeanY={m['dmeanY']:.3f} dbottomY={m['dbottomY']:.3f} "
        f"dcenterY={m['dcenterY']:.3f} MAE={m['mae']:.3f}"
    )
    print(f"  qa → {qa_path}")
    print(f"  report → {report_path}")
    if result["pass"]:
        print("FRAME0 PASS")
        return
    for e in result["errors"]:
        print(f"FAIL  {e}")
    print("FRAME0 FAIL")
    raise SystemExit(1)


def score_head_trim_candidates(
    prev_video: Path,
    next_video: Path,
    candidates: list[float],
    prev_t: float | None = None,
) -> list[dict[str, Any]]:
    """Score join quality: last frame of prev vs next@head_trim for each candidate."""
    if Image is None or np is None:
        die("Pillow + numpy 필요")
    prev_dur = video_duration_sec(prev_video)
    next_dur = video_duration_sec(next_video)
    if prev_t is None:
        prev_t = max(0.0, prev_dur - 0.05)
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="seamless-join-") as tmp:
        tmp_path = Path(tmp)
        prev_png = tmp_path / "prev.png"
        extract_frame_at(prev_video, prev_t, prev_png)
        prev_im = _load_rgb(prev_png)
        for t in candidates:
            if t < 0 or t >= next_dur - 0.05:
                continue
            # keep .png extension (do not replace dots in suffix)
            nxt_png = tmp_path / f"n_{int(round(t * 1000)):05d}.png"
            extract_frame_at(next_video, t, nxt_png)
            m = _frame_pair_metrics(prev_im, _load_rgb(nxt_png))
            rows.append({"head_trim_sec": float(t), "prev_t": float(prev_t), **m})
    rows.sort(key=lambda r: r["score"])
    return rows


def pick_best_head_trim(
    prev_video: Path,
    next_video: Path,
    max_trim: float = 2.0,
    step: float = 0.5,
    force_min_trim: float = 0.0,
) -> tuple[float, list[dict[str, Any]]]:
    """Pick head_trim in [force_min_trim, max_trim] with best join score.

    If frame0 (t=0) is already excellent, prefers low trim (cause analysis:
    fixed trim=2 can throw away the best continuity frame).
    """
    n = int(round(max_trim / step)) + 1
    cands = [round(i * step, 4) for i in range(n + 1) if i * step <= max_trim + 1e-9]
    if force_min_trim > 0:
        cands = [t for t in cands if t + 1e-9 >= force_min_trim]
        if force_min_trim not in cands:
            cands.append(round(force_min_trim, 4))
            cands = sorted(set(cands))
    rows = score_head_trim_candidates(prev_video, next_video, cands)
    if not rows:
        die("join-score: no valid candidates")
    best = rows[0]
    return float(best["head_trim_sec"]), rows


def cmd_join_score(args: argparse.Namespace) -> None:
    """Score head_trim candidates between scene N and N+1 (default 1→2)."""
    project_dir = Path(args.project_dir).resolve()
    n = int(args.scene)
    prev = scene_dir(project_dir, n) / f"scene_{n:02d}.mp4"
    nxt = scene_dir(project_dir, n + 1) / f"scene_{n + 1:02d}.mp4"
    if not prev.is_file():
        die(f"missing {prev}")
    if not nxt.is_file():
        die(f"missing {nxt}")
    max_trim = float(args.max_trim)
    step = float(args.step)
    force_min = float(args.min_trim)
    best, rows = pick_best_head_trim(prev, nxt, max_trim, step, force_min)
    report = {
        "from_scene": n,
        "to_scene": n + 1,
        "best_head_trim_sec": best,
        "max_trim": max_trim,
        "step": step,
        "min_trim": force_min,
        "candidates": rows,
    }
    out = Path(args.out).resolve() if args.out else (
        project_dir / "report" / f"join_score_s{n:02d}_s{n + 1:02d}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"join-score → best head_trim={best:g}s  (wrote {out})")
    for r in rows:
        mark = " ← BEST" if r["head_trim_sec"] == best else ""
        print(
            f"  t={r['head_trim_sec']:g}s  score={r['score']:.2f}  "
            f"MAE={r['mae']:.2f}  corr={r['corr']:.3f}  dY={r['dY']:.2f}{mark}"
        )


def cmd_concat(args: argparse.Namespace) -> None:
    """Assemble scenes. Mode A′ head_trim (doc 16): scene1 full; scene2..N skip first N sec.

    Final duration ≈ scene1 + sum(max(0, dur_i - head_trim)) for i>=2.
    Example: 2×10s gen, head_trim=2 → ~18s.
    With --auto-head-trim: pick trim per join by frame metrics (cause remediation).
    """
    project_dir = Path(args.project_dir).resolve()
    proj = load_yaml(project_dir / "project.yaml")
    scene_count = int(proj["project"]["scene_count"])
    drop_last = bool(args.drop_last_frame)

    videos: list[Path] = []
    for i in range(1, scene_count + 1):
        p = scene_dir(project_dir, i) / f"scene_{i:02d}.mp4"
        if not p.is_file():
            die(f"missing {p}")
        videos.append(p)

    # CLI overrides project.yaml assembly.head_trim_sec when set
    head_trim = float(args.head_trim) if args.head_trim is not None else 0.0
    if args.head_trim is None and not args.auto_head_trim:
        assembly = proj.get("assembly") or {}
        if str(assembly.get("mode", "")).lower() in ("head_trim", "tail_discard", "auto_head_trim"):
            head_trim = float(assembly.get("head_trim_sec") or 0.0)

    # Per-join trims: index i applies when entering video i (i>=1)
    join_trims: list[float] = [0.0] * len(videos)
    join_reports: list[dict[str, Any]] = []

    if args.auto_head_trim:
        max_trim = float(args.head_trim_max if args.head_trim_max is not None else 2.0)
        step = float(args.head_trim_step if args.head_trim_step is not None else 0.5)
        force_min = float(args.head_trim_min if args.head_trim_min is not None else 0.0)
        for i in range(1, len(videos)):
            best, rows = pick_best_head_trim(
                videos[i - 1], videos[i], max_trim, step, force_min
            )
            join_trims[i] = best
            join_reports.append(
                {
                    "into_scene": i + 1,
                    "best_head_trim_sec": best,
                    "candidates": rows,
                }
            )
            print(
                f"auto-head-trim scene{i + 1}: best={best:g}s "
                f"(score={rows[0]['score']:.2f} MAE={rows[0]['mae']:.2f} "
                f"corr={rows[0]['corr']:.3f})"
            )
        report_path = project_dir / "report" / "auto_head_trim.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {
                    "mode": "auto_head_trim",
                    "max_trim": max_trim,
                    "step": step,
                    "min_trim": force_min,
                    "joins": join_reports,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"  wrote {report_path}")
        # representative single value for logging (first join)
        head_trim = join_trims[1] if len(join_trims) > 1 else 0.0
    else:
        if head_trim < 0:
            die("head_trim must be >= 0")
        for i in range(1, len(videos)):
            join_trims[i] = head_trim

    out = Path(args.out).resolve() if args.out else project_dir / "edit" / "final_video.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="seamless-concat-") as tmp:
        tmp_path = Path(tmp)
        parts: list[Path] = []
        for i, v in enumerate(videos):
            dur = video_duration_sec(v)
            ss = float(join_trims[i]) if i > 0 else 0.0
            # drop last ~1 frame on non-final clips (optional exact cut aid)
            end_trim = (1.0 / 30.0) if (drop_last and i < len(videos) - 1) else 0.0
            usable = dur - ss - end_trim
            if usable < 0.2:
                die(
                    f"scene {i + 1}: after head_trim={ss:.2f}s (+drop_last) "
                    f"usable={usable:.3f}s too short (dur={dur:.3f}s)"
                )

            needs_trim = ss > 0 or end_trim > 0
            if needs_trim:
                part = tmp_path / f"part_{i:02d}.mp4"
                cmd = ["ffmpeg", "-y"]
                if ss > 0:
                    # accurate seek: -ss after -i for frame-accurate head trim
                    cmd += ["-i", str(v), "-ss", f"{ss:.4f}"]
                else:
                    cmd += ["-i", str(v)]
                if end_trim > 0:
                    cmd += ["-t", f"{usable:.4f}"]
                cmd += [
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    str(part),
                ]
                subprocess.check_call(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                parts.append(part)
            else:
                parts.append(v)

        # re-encode all to same params if mixed, else try stream copy via intermediate same codec
        # Safer: re-encode final concat for heterogeneous generator outputs
        list_file = tmp_path / "list.txt"
        norm_parts: list[Path] = []
        # probe first size
        w0, h0 = video_size(parts[0])
        for i, p in enumerate(parts):
            npth = tmp_path / f"norm_{i:02d}.mp4"
            subprocess.check_call(
                [
                    "ffmpeg", "-y", "-i", str(p),
                    "-vf", f"scale={w0}:{h0}:force_original_aspect_ratio=decrease,"
                           f"pad={w0}:{h0}:(ow-iw)/2:(oh-ih)/2",
                    "-r", "30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
                    str(npth),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            norm_parts.append(npth)

        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in norm_parts),
            encoding="utf-8",
        )
        subprocess.check_call(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(out),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    final_dur = video_duration_sec(out)
    print(f"concat → {out}")
    print(f"  duration≈{final_dur:.3f}s size={video_size(out)}")
    if args.auto_head_trim:
        print(f"  assembly: auto_head_trim joins={join_trims[1:]}")
    elif any(t > 0 for t in join_trims):
        print(f"  assembly: head_trim={head_trim:g}s on scenes 2..N (doc16 A′)")
        expected = None
        try:
            expected = float((proj.get("assembly") or {}).get("final_duration_sec") or 0) or None
        except (TypeError, ValueError):
            expected = None
        if expected and abs(final_dur - expected) > 0.8:
            print(f"  WARN: expected≈{expected:g}s got {final_dur:.3f}s")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Seamless Short Video handoff CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="Create project skeleton")
    init.add_argument("--project-dir", required=True)
    init.add_argument("--project-id")
    init.add_argument("--title")
    init.add_argument("--scenes", type=int, default=3)
    init.add_argument("--scene-seconds", type=float, default=10)
    init.add_argument("--aspect", default="9:16")
    init.add_argument("--transition-mode", default="last_frame_match_cut")
    init.add_argument("--generator", default="grok-imagine")
    init.add_argument(
        "--head-trim",
        type=float,
        default=0.0,
        metavar="SEC",
        help=(
            "doc16 A′: store assembly.head_trim_sec; total_duration = "
            "scene1 + (N-1)*(scene_seconds - head_trim). Example: "
            "--scenes 2 --scene-seconds 10 --head-trim 2 → 18s final."
        ),
    )
    init.set_defaults(func=cmd_init)

    handoff = sub.add_parser("handoff", help="Extract handoff frame and wire next start")
    handoff.add_argument("--project-dir", required=True)
    handoff.add_argument("--scene", type=int, required=True)
    handoff.add_argument("--video", help="Source mp4 (default scenes/scene_XX/scene_XX.mp4)")
    handoff.add_argument("--lookback", type=float, default=0.8)
    handoff.add_argument("--sample-fps", type=float, default=10.0)
    handoff.add_argument("--min-blur", type=float, default=40.0)
    handoff.set_defaults(func=cmd_handoff)

    verify = sub.add_parser("verify", help="Hard-gate verification")
    verify.add_argument("--project-dir", required=True)
    verify.add_argument("--duration-tol", type=float, default=0.6)
    verify.add_argument("--min-blur", type=float, default=40.0)
    verify.set_defaults(func=cmd_verify)

    concat = sub.add_parser(
        "concat",
        help="Match-cut concat (optional --head-trim / --auto-head-trim)",
    )
    concat.add_argument("--project-dir", required=True)
    concat.add_argument("--out")
    concat.add_argument("--drop-last-frame", action="store_true")
    concat.add_argument(
        "--head-trim",
        type=float,
        default=None,
        metavar="SEC",
        help=(
            "Discard first SEC seconds of scenes 2..N before concat "
            "(doc 16 mode A′). Example: 2×10s gen + head-trim 2 → ~18s. "
            "If omitted, uses project.yaml assembly.head_trim_sec when mode=head_trim."
        ),
    )
    concat.add_argument(
        "--auto-head-trim",
        action="store_true",
        help=(
            "Cause remediation: score join frames (prev end vs next@t) and pick "
            "best head_trim per join in [min, max] instead of fixed 2s."
        ),
    )
    concat.add_argument("--head-trim-max", type=float, default=None, metavar="SEC")
    concat.add_argument("--head-trim-min", type=float, default=None, metavar="SEC")
    concat.add_argument("--head-trim-step", type=float, default=None, metavar="SEC")
    concat.set_defaults(func=cmd_concat)

    js = sub.add_parser(
        "join-score",
        help="Score head_trim candidates between two adjacent scenes",
    )
    js.add_argument("--project-dir", required=True)
    js.add_argument("--scene", type=int, default=1, help="From scene N to N+1 (default 1)")
    js.add_argument("--max-trim", type=float, default=2.0)
    js.add_argument("--min-trim", type=float, default=0.0)
    js.add_argument("--step", type=float, default=0.5)
    js.add_argument("--out", help="JSON report path")
    js.set_defaults(func=cmd_join_score)

    f0 = sub.add_parser(
        "frame0-check",
        help="Hard-gate start_image vs video frame0 (ΔY/MAE; docs 15 C12)",
    )
    f0.add_argument("--project-dir", required=True)
    f0.add_argument("--scene", type=int, required=True, help="Scene index 1..N")
    f0.add_argument("--start-image", help="Override start image path")
    f0.add_argument("--video", help="Override scene mp4 path")
    f0.add_argument(
        "--max-dmean-y",
        type=float,
        default=DEFAULT_FRAME0_MAX_DMEAN_Y,
        help=f"|ΔmeanY| threshold (default {DEFAULT_FRAME0_MAX_DMEAN_Y})",
    )
    f0.add_argument(
        "--max-dbottom-y",
        type=float,
        default=DEFAULT_FRAME0_MAX_DBOTTOM_Y,
        help=f"|ΔbottomY| threshold (default {DEFAULT_FRAME0_MAX_DBOTTOM_Y})",
    )
    f0.add_argument(
        "--max-dcenter-y",
        type=float,
        default=DEFAULT_FRAME0_MAX_DCENTER_Y,
        help=f"|ΔcenterY| threshold (default {DEFAULT_FRAME0_MAX_DCENTER_Y})",
    )
    f0.add_argument(
        "--max-mae",
        type=float,
        default=DEFAULT_FRAME0_MAX_MAE,
        help=f"MAE threshold (default {DEFAULT_FRAME0_MAX_MAE})",
    )
    f0.set_defaults(func=cmd_frame0_check)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

"""render.py — Remotion 렌더 호출 + 세그먼트 concat + ffmpeg 오디오 mux.

리포 루트에서 `npx remotion render src/index.ts <composition>` 을 서브프로세스로 구동한다.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from .public_assets import public_roots_for_props, scoped_public_dir

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY = "src/index.ts"


def remotion_browser_executable(repo_root: str | Path) -> Path | None:
    """Return the bootstrap-managed browser without letting Remotion mutate the engine."""
    configured = os.environ.get("DRAW_PROXY_REMOTION_BROWSER")
    if configured:
        executable = Path(configured).expanduser().resolve()
        if not executable.is_file():
            raise FileNotFoundError("DRAW_PROXY_REMOTION_BROWSER does not exist")
        return executable
    manifest = Path(repo_root).resolve().parent / "runtime" / "remotion-browser" / "browser.json"
    if not manifest.is_file():
        return None
    try:
        executable = Path(json.loads(manifest.read_text(encoding="utf-8"))["executable"]).resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid Remotion browser manifest") from exc
    if not executable.is_file():
        raise FileNotFoundError("Remotion browser manifest points to a missing executable")
    return executable


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    log.info("$ %s", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def render(props_path: str | Path, out_path: str | Path, *, composition: str = "BrushLandscape",
           frames: str | None = None, concurrency: int = 4,
           repo_root: str | Path = REPO_ROOT,
           public_root: str | Path | None = None) -> Path:
    """`npx remotion render` 1회 호출. frames 는 "0-59" 형식(옵션).

    서브프로세스는 repo_root 에서 돌므로 상대 경로는 절대 경로로 변환해 넘긴다.
    """
    out = Path(out_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    resolved_public_root = Path(public_root).resolve() if public_root else Path(repo_root) / "public"
    with scoped_public_dir(props_path, public_root=resolved_public_root) as public_dir:
        roots = ", ".join(p.name for p in public_roots_for_props(
            props_path, resolved_public_root))
        log.info("render scoped public: %s", roots or "(empty)")
        cmd = ["npx", "remotion", "render", ENTRY, composition, str(out),
               f"--props={Path(props_path).resolve()}", f"--concurrency={concurrency}",
               f"--public-dir={public_dir}", "--bundle-cache=false"]
        browser = remotion_browser_executable(repo_root)
        if browser:
            cmd.append(f"--browser-executable={browser}")
        if frames:
            cmd.append(f"--frames={frames}")
        _run(cmd, cwd=Path(repo_root))
    return out


def render_segments(props_path: str | Path, out_path: str | Path, segments: list[tuple[int, int]],
                    *, composition: str = "BrushLandscape", concurrency: int = 4,
                    work_dir: str | Path | None = None, repo_root: str | Path = REPO_ROOT,
                    public_root: str | Path | None = None,
                    fps: float = 30.0) -> Path:
    """프레임 구간별 세그먼트 렌더 후 concat. segments = [(start, end), ...] (inclusive)."""
    out = Path(out_path)
    work = Path(work_dir) if work_dir else out.parent / f".{out.stem}-segments"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, (f0, f1) in enumerate(segments):
        part = work / f"seg-{i:03d}.mp4"
        expected_sec = (f1 - f0 + 1) / fps
        reusable = False
        if part.is_file():
            try:
                reusable = abs(probe_video_duration(part) - expected_sec) <= max(0.05, 1.5 / fps)
            except (OSError, ValueError, subprocess.CalledProcessError):
                reusable = False
        if reusable:
            log.info("render segment reuse: %s (%.3fs)", part.name, expected_sec)
        else:
            part.unlink(missing_ok=True)
            render(props_path, part, composition=composition, frames=f"{f0}-{f1}",
                   concurrency=concurrency, repo_root=repo_root, public_root=public_root)
            # Remotion MP4 청크에는 AAC 인코더 패딩 때문에 영상보다 약 53ms 긴
            # 무음 오디오 스트림이 포함될 수 있다. 프레임 청크의 진실은 컨테이너
            # duration이 아니라 v:0 duration이므로 영상 스트림 기준으로 판정한다.
            actual_sec = probe_video_duration(part)
            if abs(actual_sec - expected_sec) > max(0.05, 1.5 / fps):
                raise RuntimeError(
                    f"render segment duration mismatch: {part.name} "
                    f"expected={expected_sec:.3f}s actual={actual_sec:.3f}s")
        parts.append(part)
    return concat(parts, out)


def concat(parts: list[Path], out_path: str | Path) -> Path:
    """영상 세그먼트를 프레임 손실 없이 무손실 이어붙임.

    Remotion MP4 청크에는 영상보다 긴 AAC 패딩이 포함될 수 있다. concat demuxer가
    컨테이너 길이를 다음 청크의 시작 시각으로 사용하면 청크 경계마다 영상 PTS 공백이
    누적된다. 각 청크의 *영상 스트림* 길이를 명시하고 영상만 매핑해 이를 방지한다.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    list_file = out.parent / f".{out.stem}-concat.txt"
    entries = []
    for part in parts:
        entries.append(f"file '{Path(part).resolve()}'\n")
        entries.append(f"duration {probe_video_duration(part):.9f}\n")
    list_file.write_text("".join(entries), encoding="utf-8")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
          "-map", "0:v:0", "-c:v", "copy", "-an", out])
    list_file.unlink(missing_ok=True)
    return out


def mux_audio(video_path: str | Path, audio_path: str | Path, out_path: str | Path,
              *, audio_bitrate: str = "192k") -> Path:
    """영상 스트림은 copy, 오디오는 AAC 인코딩으로 mux한다.

    출력 종료점은 비디오 스트림의 정확한 프레임 길이로 고정한다. ``-shortest``는
    AAC encoder delay 때문에 600초 WAV가 599.93초로 보이는 경우 마지막 비디오
    프레임을 잘라 18,000프레임 납품 계약을 깨므로 사용하지 않는다.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    video_duration = probe_video_duration(video_path)
    _run(["ffmpeg", "-y", "-i", video_path, "-i", audio_path,
          "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
          "-c:a", "aac", "-b:a", audio_bitrate,
          "-t", f"{video_duration:.6f}", out])
    return out


def attach_cover_art(video_path: str | Path, image_path: str | Path, out_path: str | Path) -> Path:
    """MP4 재생 타임라인은 그대로 두고, Finder/Quick Look용 표지 이미지를 첨부한다.

    첫 번째 비디오 스트림과 오디오는 stream copy 한다. 표지는 attached_pic 보조 영상
    스트림이므로 재생 시점이나 첫 프레임에는 관여하지 않는다.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", video_path, "-i", image_path,
        "-map", "0:v:0", "-map", "0:a?", "-map", "1:v:0",
        "-c:v:0", "copy", "-c:a", "copy", "-c:v:1", "mjpeg",
        "-disposition:v:1", "attached_pic",
        "-metadata:s:v:1", "title=Cover Art",
        "-movflags", "+faststart", out,
    ])
    return out


def probe_duration(media_path: str | Path) -> float:
    """ffprobe 로 미디어 길이(초)."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
        capture_output=True, text=True, check=True)
    return float(res.stdout.strip())


def probe_video_duration(media_path: str | Path) -> float:
    """첫 영상 스트림 길이. AAC 패딩이 컨테이너 길이를 늘려도 프레임 길이를 보존한다."""
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
        capture_output=True, text=True, check=True)
    value = res.stdout.strip()
    if not value or value == "N/A":
        raise ValueError(f"영상 스트림 duration 없음: {media_path}")
    return float(value)

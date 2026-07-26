#!/usr/bin/env python3
"""bin/build.py — project.yaml 하나로 완성 mp4 를 만드는 단일 진입점.

스테이지: stt → cues → background → clean → routes → layout → props → render → mux → qa
산출물 캐시: data/{projectId}/stages/{stage}.json — 마커가 있으면 스킵.
재개: --from <stage> 로 해당 스테이지부터 다시 실행 (앞 스테이지는 캐시 스킵).

사용:
  pipeline/.venv/bin/python bin/build.py examples/narration/project.yaml
  pipeline/.venv/bin/python bin/build.py examples/ambient/project.yaml --from render
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DRAW_PROXY_ROOT = REPO_ROOT.parent
VENV_PY = Path(os.environ.get(
    "DRAW_PROXY_PYTHON",
    str(DRAW_PROXY_ROOT / "runtime" / "python-venv" / "bin" / "python"),
)).expanduser().resolve()

# pipeline venv 밖에서 실행되면 venv 파이썬으로 재실행 (명령 1회 원칙)
try:
    import brushvid  # noqa: F401
except ImportError:  # pragma: no cover
    if VENV_PY.is_file() and Path(sys.executable) != VENV_PY:
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise SystemExit("brushvid 미설치 — pipeline/README.md 의 부트스트랩 절차를 먼저 실행하세요")

from PIL import Image

from brushvid import audio as bv_audio
from brushvid import background as bv_bg
from brushvid import bgm as bv_bgm
from brushvid import mix as bv_mix
from brushvid import piano_auto_bgm as bv_piano_auto
from brushvid import motion as bv_motion
from brushvid import qa as bv_qa
from brushvid import render as bv_render
from brushvid import stt as bv_stt
from brushvid import tts as bv_tts
from brushvid import voice_presets as bv_voice
from brushvid.capabilities import probe_capabilities
from brushvid.tts_contract import tts_cache_signature
from brushvid.tts_manifest import build_engine_manifest, write_manifest_atomic
from brushvid.cues import FPS, group_scenes, srt_to_cues, title_color
from brushvid.cosmic_random_routes import (
    CosmicRandomRouteParams,
    generate_cosmic_random_routes,
    write_cosmic_random_routes,
)
from brushvid.layout import validate_layout
from brushvid.layers import alpha_line_thickness, estimate_line_thickness, prepare_pen_brush_layers
from brushvid.fill_routes import generate_fill_routes, write_fill_routes
from brushvid.project import BgmConfig, ProjectConfig, load_project
from brushvid.runtime import RuntimeContext, RuntimePathError, StageEventWriter, sanitize_payload
from brushvid.props import (BRUSH_NO_PULSE_PRESET, build_props, build_scene,
                            default_brush, fit_brush_completion_timing, load_schema,
                            validate_brush_completion_timing, write_props)
from brushvid.routes import RouteParams, generate_routes, write_routes
from brushvid.sync import apply_sync, retime_range, snap_pen_brush_boundary

log = logging.getLogger("build")

STAGES = ["stt", "cues", "background", "clean", "routes", "sync", "layout", "props", "render", "mix", "mux", "qa"]

# pen 프로파일 상수 (dev-plan pen Phase 1 "핵심 발견" 3요소: 잉크-알파 분리 + 정밀 routes + contain 배경)
PEN_PAPER_HEX = "#f2eee3"
PEN_BRUSH = {"kind": "pen", "w": 140}
PAINT_BRUSH = {"kind": "image", "src": "brush-draw/brush.png", "w": 300, "h": 186,
               "tipx": 14, "tipy": 170}
PEN_SCENE_PRESET = {
    "faint": 1.0, "edgeFeather": 1, "developFrames": 8,
    "completionMode": "integrated-develop",
    "previewOpacity": 0,
    "prewashOpacity": 0, "prewashFrames": 0, "prewashHoldFrames": 0,
    # 완성 이미지에서 다음 씬의 종이 화면으로 1프레임 하드컷되지 않도록 충분히 긴 dissolve.
    # 고대비 장면도 마지막 1프레임에 번쩍이지 않도록 36f(1.2s)로 균일하게 녹인다.
    "outroFadeFrames": 36, "outroWashOpacity": 1.0, "outroBlur": 0,
}
# 펜브러쉬는 마지막 색칠 결과를 충분히 보여줘야 한다. pen 단독 프로파일의
# 종이 전환용 outro를 공유하면 완성 직후 다시 하얗게 씻겨 저품질처럼 보인다.
PEN_BRUSH_SCENE_PRESET = {
    "faint": 1.0, "edgeFeather": 0.8, "developFrames": 8,
    "completionMode": "integrated-develop",
    "previewOpacity": 0,
    "prewashOpacity": 0, "prewashFrames": 0, "prewashHoldFrames": 0,
    "outroFadeFrames": 0, "outroWashOpacity": 0, "outroBlur": 0,
}
PEN_BRUSH_DYNAMICS = {"drawSpeedScale": 1.0, "touchScale": 1.0, "touchJitter": 0,
                      "pathJitter": 0, "randomizeOrder": False, "randomReverse": False}

# 카드형 플레이어는 MP4 attached_pic보다 실제 v:0의 0프레임을 우선할 수 있다.
# 첫 씬만 정규화된 원본을 흐리게 보여준 뒤 종이로 지우고, 같은 12f 지연 뒤 첫 획을 시작한다.
OPENING_POSTER_FRAMES = 12  # 30fps 기준 0.40초
BRUSH_FIRST_SCENE_BLURRED_POSTER = {
    "prewashOpacity": 0.50,
    "prewashFrames": OPENING_POSTER_FRAMES,
    "prewashHoldFrames": 0,
    "prewashFadeOutFrames": OPENING_POSTER_FRAMES,
    "prewashBlur": 12,
}
PEN_FIRST_SCENE_BLURRED_POSTER = {
    "prewashOpacity": 0.35,
    "prewashFrames": OPENING_POSTER_FRAMES,
    "prewashHoldFrames": 0,
    "prewashFadeOutFrames": OPENING_POSTER_FRAMES,
    "prewashBlur": 10,
}


def pen_route_params(duration: int, seed: int, *, draw_start: float = 8) -> RouteParams:
    """pen 프로파일 정밀 routes 파라미터 — drawEnd 를 앞당겨(35%) 감상 구간 확보."""
    draw_end = round(duration * 0.35)
    return RouteParams(
        duration=duration, draw_start=draw_start, draw_end=draw_end, pen_invisible_after=draw_end + 8,
        seed=seed, analyze_scale=1.5, contour_width=18, rdp_eps=1.5, max_len=300,
        min_route_len=12, seal_width=24, seal_step=18,
        group_by_zone=True,  # 매크로 존(오브젝트) 단위 드로잉 순서
    )


# 캔버스 치수 (format → width, height)
CANVAS = {"youtube": (1920, 1080), "shorts": (1080, 1920)}
UHD_CANVAS = (3840, 2160)
FULL_BLEED_PROFILES = {"progressive-frame-sequence", "storybook-full-bleed"}
FULL_COLOR_MOTION_PROFILE = "full-color-motion"
# 한 프로젝트만 렌더 큐에 넣는 전제에서, UHD 청크 내부 병렬 인코더 수.
# 10-core / 24 GB 워크스테이션에서 100초 청크 6개를 안정적으로 처리하는 값이다.
UHD_RENDER_CONCURRENCY = 4


def resolve_canvas(fmt: str, resolution: str) -> tuple[int, int]:
    if fmt == "youtube" and resolution == "uhd":
        return UHD_CANVAS
    return CANVAS[fmt]


def resolve_composition(cfg: ProjectConfig) -> str:
    if cfg.drawing_profile == FULL_COLOR_MOTION_PROFILE:
        if cfg.fmt == "shorts":
            return "FullColorMotionPortrait"
        return "FullColorMotionLandscape4K" if cfg.render_resolution == "uhd" else "FullColorMotionLandscape"
    if cfg.fmt == "shorts":
        return "BrushPortrait"
    return "BrushLandscape4K" if cfg.render_resolution == "uhd" else "BrushLandscape"


def scale_brush(brush: dict, scale: float) -> dict:
    """캔버스 좌표계가 UHD가 되어도 커서의 상대 크기를 유지한다."""
    if abs(scale - 1.0) < 1e-9:
        return dict(brush)
    out = dict(brush)
    for key in ("w", "h", "tipx", "tipy"):
        if key in out:
            out[key] = round(float(out[key]) * scale, 3)
    return out


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _generated_piano_manifest_approved(
    cfg: ProjectConfig, *, repo_root: Path | None = None,
    output_root: Path | None = None,
) -> bool:
    """Return whether this project has a human-approved generated BGM manifest."""
    repo_root = REPO_ROOT if repo_root is None else repo_root
    output_root = repo_root / "output" if output_root is None else output_root
    manifest = output_root / "original-audio" / "piano-bgm" / cfg.project_id / "generated-bgm-manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("kind") == "generated-piano-bgm" and data.get("status") == "APPROVED"


def preflight_project(cfg: ProjectConfig, *, final: bool = False,
                      verify_sources: bool = False,
                      runtime: RuntimeContext | None = None) -> dict:
    """렌더 전에 런타임·4K 원본·BGM 계약을 확인한다.

    ``final``은 실제 납품 큐 전용이다. synth/암묵적 auto BGM과 FHD, 누락된 source
    manifest를 허용하지 않는다. 명시적 ``bgm.mode: piano-auto``는 사람 청취 승인
    manifest가 있어야 하며, 명시적 ``off``는 BGM 없는 AAC 무음 트랙 납품으로 인정한다.
    """
    errors: list[str] = []
    warnings: list[str] = []
    runtime = runtime or RuntimeContext.standalone(REPO_ROOT)
    executables = {name: shutil.which(name) is not None for name in ("ffmpeg", "ffprobe", "npx", "node")}
    missing_executables = [name for name, available in executables.items() if not available]
    if missing_executables:
        errors.append("실행 파일 없음: " + ", ".join(missing_executables))
    if not (runtime.engine_root / "node_modules").is_dir():
        errors.append("engine node_modules 없음")
    if not VENV_PY.is_file():
        errors.append("Python runtime 없음")

    if final:
        if cfg.fmt != "youtube" or cfg.render_resolution != "uhd":
            errors.append("최종 납품은 format: youtube + render.resolution: uhd 여야 함")
        if cfg.mode != "ambient" or cfg.ambient_scenes != 60 or len(cfg.ambient_cues) != 60:
            errors.append("최종 납품은 ambient 60 scenes + 60 cues 여야 함")
        if cfg.bg_strategy != "user-images" or cfg.bg_fit != "cover" or len(cfg.bg_images) != 60:
            errors.append("최종 납품은 user-images 60장 + fit: cover 여야 함")
        if cfg.bgm is None or cfg.bgm.mode not in {"off", "asset", "playlist", "piano-auto"}:
            errors.append("최종 납품 BGM은 명시적 off, asset, playlist 또는 승인된 piano-auto여야 함 (암묵적 auto/synth 불가)")
        elif cfg.bgm.mode in {"asset", "playlist"} and cfg.bgm.license_policy != "strict":
            errors.append("최종 납품의 asset/playlist BGM은 strict license policy가 필요함")
        elif cfg.bgm.mode == "piano-auto":
            if not _generated_piano_manifest_approved(cfg, output_root=runtime.output_root):
                errors.append("piano-auto 최종 납품에는 generated-bgm-manifest.json APPROVED가 필요함")
        elif cfg.bgm.mode == "off":
            warnings.append("BGM off — AAC 무음 트랙으로 납품, BGM 라이선스 매니페스트는 생성하지 않음")
    elif cfg.bgm and cfg.bgm.mode == "synth":
        warnings.append("synth BGM — 파일럿 전용, 최종 납품에는 사용할 수 없음")

    if cfg.bgm and cfg.bgm.mode in {"asset", "playlist"}:
        try:
            assets = bv_bgm.preflight_assets(cfg.bgm, distribution=cfg.fmt, repo_root=runtime.engine_root)
        except bv_bgm.BgmAssetError as exc:
            errors.append(str(exc))
        else:
            warnings.append("BGM preflight 통과: " + ", ".join(asset["id"] for asset in assets))

    image_checks = []
    for image in cfg.bg_images:
        try:
            with Image.open(image) as source:
                width, height = source.size
        except (OSError, ValueError) as exc:
            errors.append(f"원본 이미지 열기 실패: {image} ({exc})")
            continue
        kind = "final_4k" if "final_4k" in image.parts else ("4k" if "4k" in image.parts else None)
        image_checks.append({"path": image.name, "width": width, "height": height, "sourceKind": kind})
        if final and (kind is None or (width, height) != UHD_CANVAS):
            errors.append(f"최종 원본은 4k/final_4k의 3840×2160 PNG여야 함: {image} ({width}×{height})")

    manifest_path = cfg.base_dir / "source-manifest.json"
    manifest_verified = False
    if final:
        if not manifest_path.is_file():
            errors.append(f"source manifest 없음: {manifest_path}")
        else:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                scenes = manifest.get("scenes") or []
                expected_paths = [str(p) for p in cfg.bg_images]
                manifest_paths = [str(s.get("sourceAbsPath", "")) for s in scenes]
                render_meta = manifest.get("render") or {}
                resolution_meta = manifest.get("resolution") or {}
                manifest_width = render_meta.get("width", resolution_meta.get("width"))
                manifest_height = render_meta.get("height", resolution_meta.get("height"))
                # 초기 병렬 워커가 작성한 manifest는 `fps/frameCount/durationSeconds/
                # resolution` 이름을 쓸 수 있다. scene별 필수 원본 계약은 동일하므로
                # 두 스키마를 모두 읽고 납품 전 불필요한 재생성을 막는다.
                manifest_fps = manifest.get("frameRate", manifest.get("fps", render_meta.get("fps")))
                scene_duration = manifest.get("sceneDurationSeconds", 10)
                manifest_frames = manifest.get("totalFrames", manifest.get(
                    "frameCount", manifest.get("sceneCount", 0) * scene_duration * (manifest_fps or 0)))
                manifest_duration = manifest.get("totalDurationSec", manifest.get(
                    "durationSeconds", manifest.get("sceneCount", 0) * scene_duration))
                valid_header = (
                    manifest.get("projectId") == cfg.project_id
                    and manifest.get("sceneCount") == 60
                    and manifest_fps == 30
                    and manifest_frames == 18_000
                    and manifest_duration == 600
                    and manifest_width == 3840
                    and manifest_height == 2160
                )
                if not valid_header or manifest_paths != expected_paths or len(scenes) != 60:
                    errors.append(f"source manifest 계약 불일치: {manifest_path}")
                for i, scene in enumerate(scenes, start=1):
                    profile_ok = scene.get("profile") in {cfg.drawing_profile, "dark-random-brush"}
                    scene_ok = (
                        scene.get("sceneNo") == i
                        and scene.get("width") == 3840 and scene.get("height") == 2160
                        and scene.get("fit") == "cover"
                        and scene.get("seed") in {cfg.seed, cfg.seed + (i - 1) * 37}
                        and scene.get("durationFrames", 300) == 300
                        and scene.get("durationSec", 10) == 10
                        and profile_ok
                        and isinstance(scene.get("cue"), str) and bool(scene["cue"].strip())
                    )
                    if not scene_ok:
                        errors.append(f"source manifest scene {i} 계약 불일치")
                        break
                if verify_sources and not errors:
                    for record, image in zip(scenes, cfg.bg_images):
                        recorded_sha = record.get("sha256", record.get("sourceSha256"))
                        if _sha256_file(image) != recorded_sha:
                            errors.append(f"source manifest SHA-256 불일치: {image}")
                            break
                manifest_verified = not errors
            except (OSError, ValueError, json.JSONDecodeError, KeyError) as exc:
                errors.append(f"source manifest 파싱 실패: {manifest_path} ({exc})")

    report = {
        "projectId": cfg.project_id,
        "final": final,
        "verifySources": verify_sources,
        "canvas": {"width": resolve_canvas(cfg.fmt, cfg.render_resolution)[0],
                   "height": resolve_canvas(cfg.fmt, cfg.render_resolution)[1], "fps": FPS},
        "executables": executables,
        "capabilities": probe_capabilities(runtime),
        "images": image_checks,
        "sourceManifest": str(manifest_path) if manifest_path.is_file() else None,
        "sourceManifestVerified": manifest_verified,
        "warnings": warnings,
        "errors": errors,
        "pass": not errors,
    }
    if errors:
        raise SystemExit("preflight 실패:\n- " + "\n- ".join(errors))
    return sanitize_payload(report, runtime)

# 쇼츠(세로) 연출 프리셋 — shorts + 앰비언트에서 props 스테이지가 주입
SHORTS_SUBTITLE_STYLE = {"bottom": 290, "maxWidth": 900, "fontSize": 36}  # 하단 세이프존 기본
SHORTS_FIRST_PREWASH = {"prewashOpacity": 0.5, "prewashFrames": 18,
                        "prewashHoldFrames": 6, "prewashBlur": 12}  # 첫 씬 훅(짧은 프리워시)
SHORTS_OUTRO_FADE_FRAMES = 18       # 모든 씬 전환 outro 페이드
SHORTS_LOOP_WASH_OPACITY = 1.0      # 마지막 씬 순백 수렴 (루프 친화)

# 앰비언트 모드 기본값
AMBIENT_SCENE_FRAMES = 300
AMBIENT_DEFAULT_CUES = [
    "바람이 지나간 자리에 고요가 남는다",
    "숲은 서두르지 않는다",
    "눈 내린 아침, 세상이 한 뼘 느려진다",
    "물소리는 길을 묻지 않는다",
    "노을은 하루를 천천히 접는다",
]


class StageLedger:
    """스테이지 마커(data/{pid}/stages/*.json) 기반 캐시/스킵/재개 관리."""

    def __init__(self, stages_dir: Path):
        self.dir = stages_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def marker(self, stage: str) -> Path:
        return self.dir / f"{stage}.json"

    def is_done(self, stage: str) -> bool:
        return self.marker(stage).is_file()

    def payload(self, stage: str) -> dict:
        return json.loads(self.marker(stage).read_text(encoding="utf-8"))

    def mark_done(self, stage: str, payload: dict | None = None) -> None:
        data = {"stage": stage, "completedAt": time.strftime("%Y-%m-%d %H:%M:%S"), **(payload or {})}
        marker = self.marker(stage)
        temporary = marker.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(marker)

    def invalidate_from(self, stage: str) -> None:
        """stage 및 이후 스테이지 마커 제거 (--from 재개)."""
        if stage not in STAGES:
            raise ValueError(f"--from 스테이지는 {STAGES} 중 하나여야 함 (입력: {stage!r})")
        for s in STAGES[STAGES.index(stage):]:
            self.marker(s).unlink(missing_ok=True)


class Pipeline:
    """스테이지 오케스트레이션. 경로 규약:

    data/{pid}/   — props.json, scenes.json, stages/, qa/, bgm.wav
    public/{pid}/ — bg/*.png, routes/*.routes.json (Remotion staticFile 대상)
    output/{pid}.mp4 — 최종 산출물
    """

    def __init__(self, cfg: ProjectConfig, *, runtime: RuntimeContext | None = None,
                 events: StageEventWriter | None = None):
        self.cfg = cfg
        self.runtime = runtime or RuntimeContext.standalone(REPO_ROOT)
        self.events = events
        self.canvas = resolve_canvas(cfg.fmt, cfg.render_resolution)
        pid = cfg.project_id
        self.data_dir = self.runtime.data_root / pid
        self.public_dir = self.runtime.public_root / pid
        self.output = self.runtime.output_root / f"{pid}.mp4"
        self.video_only = self.data_dir / "video-noaudio.mp4"
        self.scenes_json = self.data_dir / "scenes.json"
        self.props_json = self.data_dir / "props.json"
        self.ledger = StageLedger(self.data_dir / "stages")
        self._bgm_assets: list[dict] = []

    # ── 실행 ──
    def run(self, from_stage: str | None = None) -> Path:
        # 외부 음원은 비싼 Remotion 렌더 전에 로컬 파일·해시·증빙을 검증한다.
        if self.cfg.bgm is not None and self.cfg.bgm.mode in ("asset", "playlist"):
            self._bgm_assets = bv_bgm.preflight_assets(
                self.cfg.bgm, distribution=self.cfg.fmt, repo_root=self.runtime.engine_root)
        if from_stage:
            self.ledger.invalidate_from(from_stage)
        # voice ID, speed, 대본, catalog 또는 고정 style이 바뀌면 TTS부터 다시 만든다.
        if self.cfg.mode == "tts" and self.ledger.is_done("stt"):
            old_sig = self.ledger.payload("stt").get("signature")
            expected = self._tts_signature()
            required = [
                self.data_dir / "tts" / "narration.wav",
                self.data_dir / "tts" / "narration.srt",
                self.data_dir / "tts" / "voice-manifest.json",
            ]
            if old_sig != expected or not all(path.is_file() for path in required):
                log.info("TTS 설정/대본/음성팩 변경 또는 산출물 누락 — stt 이후 캐시 무효화")
                self.ledger.invalidate_from("stt")
        # user-images 입력은 파일 내용이 바뀌어도 경로가 같을 수 있다. 예전에는
        # background 마커만 보고 스킵해서 public/{pid}/bg와 routes가 이전 이미지인
        # 채로 props/render만 다시 실행되는 캐시 오염이 가능했다.
        if self.cfg.bg_strategy == "user-images" and self.ledger.is_done("background"):
            old_sig = self.ledger.payload("background").get("signature")
            new_sig = self._background_signature()
            bg_missing = any(
                not (self.public_dir / "bg" / f"scene-{i + 1:02d}.png").is_file()
                for i in range(len(self._scenes()))
            ) if self.scenes_json.is_file() else True
            if old_sig != new_sig or bg_missing:
                log.info("배경 원본 변경/누락 감지 — background 이후 캐시 무효화")
                self.ledger.invalidate_from("background")
        # mix 스테이지 도입 전 캐시나 BGM 설정/원본이 바뀐 캐시는 오디오부터 무효화한다.
        if not self.ledger.is_done("mix") and self.ledger.is_done("mux"):
            self.ledger.invalidate_from("mix")
        elif self.ledger.is_done("mix"):
            old_sig = self.ledger.payload("mix").get("signature")
            # 테스트/외부 확장 스테이지가 signature를 기록하지 않은 경우는 기존 ledger 의미를 존중한다.
            if old_sig is not None and old_sig != self._mix_signature():
                log.info("BGM/오디오 설정 변경 감지 — mix 이후 캐시 무효화")
                self.ledger.invalidate_from("mix")
        for stage in STAGES:
            fn = getattr(self, f"stage_{stage}")
            if self.ledger.is_done(stage):
                marker = self.runtime.relative(self.ledger.marker(stage))
                log.info("[skip] %s (캐시: %s)", stage, marker)
                if self.events:
                    self.events.emit(stage, "skipped", {"marker": marker})
                continue
            log.info("[run ] %s", stage)
            if self.events:
                self.events.emit(stage, "started")
            try:
                payload = fn() or {}
                self.ledger.mark_done(stage, payload)
            except BaseException as exc:
                if self.events:
                    self.events.emit(stage, "failed", {"errorType": type(exc).__name__})
                raise
            if self.events:
                self.events.emit(stage, "completed", payload)
        log.info("완료 → %s", self.runtime.relative(self.output))
        return self.output

    # ── 공용 헬퍼 ──
    def _scenes(self) -> list[dict]:
        return json.loads(self.scenes_json.read_text(encoding="utf-8"))

    def _write_scenes(self, scenes: list[dict]) -> None:
        self.scenes_json.parent.mkdir(parents=True, exist_ok=True)
        self.scenes_json.write_text(json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8")

    def _uses_first_scene_blurred_poster(self, scene_index: int) -> bool:
        """종이 썸네일을 흐린 원본으로 바꾸는 첫 씬 전용 계약.

        shorts 앰비언트는 이미 별도 18f 훅을 갖고, preserveSource pen은 명시적인
        32% 원본 가이드가 연출 목적이므로 이 표준 포스터에서 제외한다.
        """
        if scene_index != 0:
            return False
        if self.cfg.drawing_profile == "pen":
            return not self.cfg.drawing_preserve_source
        return self.cfg.drawing_profile == "brush" and not (
            self.cfg.fmt == "shorts" and self.cfg.mode == "ambient"
        )

    def _first_scene_poster_image(self) -> str:
        return f"{self.cfg.project_id}/bg/scene-01.png"

    def _voice_path(self) -> Path | None:
        """내레이션/whisper/TTS 음성 경로. BGM은 이 경로에 섞지 않는다."""
        if self.cfg.audio is not None:
            return self.cfg.audio
        if self.cfg.mode == "tts":
            return self.data_dir / "tts" / "narration.wav"
        return None

    def _mix_signature(self) -> str:
        cfg = None if self.cfg.bgm is None else {
            "mode": self.cfg.bgm.mode, "assetId": self.cfg.bgm.asset_id,
            "assetIds": list(self.cfg.bgm.asset_ids), "gainDb": self.cfg.bgm.gain_db,
            "sourceStartSec": self.cfg.bgm.source_start_sec,
            "fadeInSec": self.cfg.bgm.fade_in_sec, "fadeOutSec": self.cfg.bgm.fade_out_sec,
            "crossfadeSec": self.cfg.bgm.crossfade_sec,
            "duckingEnabled": self.cfg.bgm.ducking_enabled,
            "duckingAmountDb": self.cfg.bgm.ducking_amount_db,
            "duckingAttackMs": self.cfg.bgm.ducking_attack_ms,
            "duckingReleaseMs": self.cfg.bgm.ducking_release_ms,
            "licensePolicy": self.cfg.bgm.license_policy,
            "prompt": self.cfg.bgm.prompt,
            "negativePrompt": self.cfg.bgm.negative_prompt,
            "cfg": self.cfg.bgm.cfg,
            "steps": self.cfg.bgm.steps,
        }
        payload = {"projectMode": self.cfg.mode, "bgm": cfg,
                   "assets": [{"id": a["id"], "sha256": a["sha256"]} for a in self._bgm_assets]}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _tts_signature(self) -> str:
        """대본과 해석된 preset 계약의 결정적 서명."""
        if self.cfg.mode != "tts" or self.cfg.tts is None:
            return ""
        if self.cfg.tts.get("engine", "supertonic") == "supertonic":
            return bv_voice.tts_signature(self.cfg.tts_text(), self.cfg.tts)
        return tts_cache_signature(self.cfg.tts_text(), self.cfg.tts)

    def _background_signature(self) -> str:
        """배경 설정과 user-images 파일 내용의 결정적 서명."""
        images = []
        for path in self.cfg.bg_images:
            images.append({
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        payload = {
            "strategy": self.cfg.bg_strategy,
            "fit": self.cfg.bg_fit,
            "subject": self.cfg.bg_subject,
            "canvas": self.canvas,
            "renderResolution": self.cfg.render_resolution,
            "seed": self.cfg.seed,
            "images": images,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    def _render_input_signature(self) -> str:
        """중단 재개 청크가 현재 이미지·routes·props와 같은 입력인지 식별한다."""
        h = hashlib.sha256()
        h.update(self.props_json.read_bytes())
        h.update(self.ledger.payload("background").get("signature", "").encode())
        routes_dir = self.public_dir / "routes"
        for path in sorted(routes_dir.glob("*.json")):
            h.update(path.name.encode())
            h.update(path.read_bytes())
        return h.hexdigest()[:16]

    # ── 스테이지 ──
    def stage_stt(self) -> dict:
        """stt 자리 분기: whisper 는 전사, tts 는 더빙 합성 (둘 다 SRT 산출)."""
        if self.cfg.mode == "tts":
            res = bv_tts.synthesize_narration(
                self.cfg.tts_text(),
                self.data_dir / "tts" / "narration.wav",
                self.data_dir / "tts" / "narration.srt",
                engine=self.cfg.tts["engine"], voice=self.cfg.tts["voice"],
                speed=self.cfg.tts.get("speed", 1.05), pause_ms=self.cfg.tts["pauseMs"],
                lang=self.cfg.tts.get("language", "ko"),
                reference=self.cfg.tts.get("reference"),
                work_root=self.data_dir / "tts" / ".work",
            )
            manifest_path = self.data_dir / "tts" / "voice-manifest.json"
            if self.cfg.tts.get("engine", "supertonic") == "supertonic":
                manifest = {
                    "schemaVersion": 1,
                    "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "projectId": self.cfg.project_id,
                    **res["voice"],
                    "pauseMs": self.cfg.tts["pauseMs"],
                    "timing": self.cfg.tts.get("timing", "tts"),
                    "requestedTiming": self.cfg.tts.get("timing", "tts"),
                    "appliedTiming": "tts",
                    "durationSec": res["durationSec"],
                    "sentenceCount": len(res["entries"]),
                }
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = manifest_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                tmp.replace(manifest_path)
            else:
                manifest = build_engine_manifest(
                    project_id=self.cfg.project_id,
                    config=self.cfg.tts,
                    result=res,
                )
                write_manifest_atomic(manifest, manifest_path)
            return {
                "srt": self.runtime.relative(res["srt"]),
                "wav": self.runtime.relative(res["wav"]), "durationSec": res["durationSec"],
                "sentences": len(res["entries"]), "signature": self._tts_signature(),
                "voiceManifest": self.runtime.relative(manifest_path), "voice": res["voice"],
            }
        if self.cfg.mode != "whisper":
            log.info("stt: %s 모드 — 해당 없음", self.cfg.mode)
            return {"skippedReason": self.cfg.mode}
        srt = bv_stt.transcribe(
            self.cfg.audio, self.data_dir / "stt",
            model_root=self.runtime.model_root / "faster-whisper",
        )
        return {"srt": self.runtime.relative(srt)}

    def stage_cues(self) -> dict:
        if self.cfg.mode == "ambient":
            lines = self.cfg.ambient_cues or AMBIENT_DEFAULT_CUES
            scenes = []
            for i in range(self.cfg.ambient_scenes):
                text = lines[i % len(lines)]
                scenes.append({
                    "durationInFrames": AMBIENT_SCENE_FRAMES,
                    "cues": [{"text": text, "from": 40, "to": AMBIENT_SCENE_FRAMES - 40}],
                })
        else:
            # tts/whisper 는 stt 스테이지 산출 SRT 사용 (tts 모드는 TTS duration이 타이밍의 시계)
            srt_path = self.cfg.srt if self.cfg.mode == "narration" \
                else self.runtime.resolve(self.ledger.payload("stt")["srt"])
            cues = srt_to_cues(srt_path.read_text(encoding="utf-8"), fps=FPS)
            scenes = group_scenes(cues)
            # TC-4.E2: 더빙 오디오 길이와 씬 합산 정합 (>1s 불일치 시 경고+보정)
            timing_audio = self.cfg.audio if self.cfg.audio is not None \
                else (self._voice_path() if self.cfg.mode == "tts" else None)
            if timing_audio is not None:
                bv_audio.reconcile_scenes_with_audio(scenes, bv_audio.probe_duration(timing_audio), fps=FPS)
        self._write_scenes(scenes)
        return {"sceneCount": len(scenes), "totalFrames": sum(s["durationInFrames"] for s in scenes)}

    def stage_background(self) -> dict:
        results = []
        scene_count = len(self._scenes())
        for i in range(scene_count):
            out = self.public_dir / "bg" / f"scene-{i + 1:02d}.png"
            if self.cfg.drawing_profile == "cosmic-random-brush" and self.cfg.bg_strategy == "user-images":
                bv_bg.compose_dark_rgba(self.cfg.bg_images[i], out, size=self.canvas, fit=self.cfg.bg_fit)
                results.append("user-images-dark-rgba")
            else:
                # background.images는 문서 계약대로 씬 순서 목록이다. 전체 목록을
                # 매 씬에 넘기면 N장을 가로 몽타주해 모든 씬이 같은 이미지가 된다.
                images = None
                if self.cfg.bg_strategy == "user-images":
                    images = [self.cfg.bg_images[i % len(self.cfg.bg_images)]]
                res = bv_bg.generate(self.cfg.bg_strategy, out, subject=self.cfg.bg_subject,
                                     images=images, seed=self.cfg.seed + i * 37,
                                     size=self.canvas, fit=self.cfg.bg_fit,
                                     imagegen_fallback=self.cfg.bg_imagegen_fallback)
                results.append(res["strategy"])
        return {"strategies": results, "signature": self._background_signature()}

    def stage_clean(self) -> dict:
        if self.cfg.drawing_profile == FULL_COLOR_MOTION_PROFILE:
            # Full Color Motion은 원본 색면을 그대로 움직인다. clean/content 분리는
            # pen/brush 전용 전처리이므로 여기서 새 bitmap을 만들지 않는다.
            return {"profile": FULL_COLOR_MOTION_PROFILE, "preservedSource": True}
        if self.cfg.drawing_profile in FULL_BLEED_PROFILES:
            # 원본 프레임의 시간적 진행/캐릭터 일관성이 연출의 핵심이므로 종이 clean을 적용하지 않는다.
            for i in range(len(self._scenes())):
                src = self.public_dir / "bg" / f"scene-{i + 1:02d}.png"
                dst = self.public_dir / "bg" / f"scene-{i + 1:02d}-content.png"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            return {"profile": self.cfg.drawing_profile, "preservedFullBleedSource": True}
        if self.cfg.drawing_profile == "cosmic-random-brush":
            # 다크 원본을 종이색 clean으로 변형하지 않고 그대로 리빌 대상으로 사용한다.
            for i in range(len(self._scenes())):
                src = self.public_dir / "bg" / f"scene-{i + 1:02d}.png"
                dst = self.public_dir / "bg" / f"scene-{i + 1:02d}-content.png"
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            return {"profile": "cosmic-random-brush", "preservedDarkSource": True}
        if self.cfg.drawing_profile == "pen-brush":
            metrics = []
            for i in range(len(self._scenes())):
                stem = self.public_dir / "bg" / f"scene-{i + 1:02d}"
                result = prepare_pen_brush_layers(
                    stem.with_suffix(".png"),
                    Path(f"{stem}-outline.png"), Path(f"{stem}-outline-flat.png"),
                    Path(f"{stem}-color.png"), size=self.canvas,
                    content_path=Path(f"{stem}-content.png"),
                    allow_full_bleed=self.cfg.drawing_full_bleed,
                )
                metrics.append({"contentFraction": round(result["contentFraction"], 6),
                                "outlineFraction": round(result["outlineFraction"], 6),
                                "lineThickness": round(result["lineThickness"], 4),
                                "fullBleed": result["fullBleed"]})
            return {"profile": "pen-brush", "layers": metrics}
        if self.cfg.drawing_profile == "pen":
            # pen: 준비된 캔버스에서 잉크-알파 분리. background.fit=cover이면 full-bleed 유지.
            fracs = []
            for i in range(len(self._scenes())):
                bg = self.public_dir / "bg" / f"scene-{i + 1:02d}.png"
                res = bv_bg.separate_ink(bg, self.public_dir / "bg" / f"scene-{i + 1:02d}-ink.png",
                                         self.public_dir / "bg" / f"scene-{i + 1:02d}-content.png",
                                         size=self.canvas)
                fracs.append(round(res["inkFraction"], 4))
            return {"profile": "pen", "inkFractions": fracs}
        for i in range(len(self._scenes())):
            bg = self.public_dir / "bg" / f"scene-{i + 1:02d}.png"
            bv_bg.clean(bg, self.public_dir / "bg" / f"scene-{i + 1:02d}-content.png")
        return {}

    def stage_routes(self) -> dict:
        pen = self.cfg.drawing_profile == "pen"
        pen_brush = self.cfg.drawing_profile == "pen-brush"
        cosmic = self.cfg.drawing_profile == "cosmic-random-brush"
        if self.cfg.drawing_profile == FULL_COLOR_MOTION_PROFILE:
            assert self.cfg.motion is not None
            reveal_scenes = []
            for i, scene in enumerate(self._scenes()):
                spec = self.cfg.motion.scene_for(i)
                if spec.reveal != "brush":
                    continue
                source = self.public_dir / "bg" / f"scene-{i + 1:02d}.png"
                coordinate_scale = self.canvas[0] / 1920
                params = RouteParams(
                    duration=scene["durationInFrames"], draw_start=0,
                    draw_end=spec.reveal_frames,
                    pen_invisible_after=spec.reveal_frames,
                    seed=self.cfg.seed + i * 37,
                    contour_width=44 * coordinate_scale,
                    seal_width=68 * coordinate_scale,
                    seal_step=18 * coordinate_scale,
                )
                params.width, params.height = self.canvas
                data = generate_routes(
                    source, params,
                    image_rel=f"{self.cfg.project_id}/bg/scene-{i + 1:02d}.png",
                )
                # Motion renderer는 마지막 28%에 전체 fill을 섞어 색면 누락을 막지만,
                # 실제 붓 경로는 분석된 원본 위에서 먼저 진행되어야 한다.
                if not data.get("strokes"):
                    raise SystemExit(f"scene-{i + 1:02d}: motion brush reveal routes가 비어 있음")
                # motion_route_rel은 staticFile/public 기준 경로다. self.public_dir는
                # 이미 public/<projectId>이므로 여기에서 projectId를 한 번 더 붙이면
                # scoped renderer가 404를 낸다.
                route_path = self.public_dir.parent / bv_motion.motion_route_rel(self.cfg.project_id, i)
                write_routes(data, route_path)
                reveal_scenes.append({"sceneId": f"scene-{i + 1:02d}",
                                      "routes": str(route_path.relative_to(self.public_dir)),
                                      "coverage": data["meta"].get("coverage")})
            if not reveal_scenes:
                log.info("routes: full-color-motion brush reveal 없음 — routes 생략")
            return {"profile": FULL_COLOR_MOTION_PROFILE,
                    "brushRevealScenes": reveal_scenes}
        if self.cfg.drawing_profile in FULL_BLEED_PROFILES:
            log.info("routes: profile=%s — 원본 frame sequence/full-bleed presentation 사용", self.cfg.drawing_profile)
            return {"skippedReason": self.cfg.drawing_profile}
        coverages = []
        for i, scene in enumerate(self._scenes()):
            content = self.public_dir / "bg" / f"scene-{i + 1:02d}-content.png"
            d = scene["durationInFrames"]
            seed = self.cfg.seed + i * 37
            opening_draw_start = 0 if self._uses_first_scene_blurred_poster(i) else 8
            if cosmic:
                image_rel = f"{self.cfg.project_id}/bg/scene-{i + 1:02d}-content.png"
                coordinate_scale = self.canvas[0] / 1920
                params = CosmicRandomRouteParams(
                    width=self.canvas[0], height=self.canvas[1], duration=d, seed=seed,
                    brush_min=230 * coordinate_scale, brush_max=365 * coordinate_scale,
                    length_min=320 * coordinate_scale, length_max=610 * coordinate_scale,
                )
                data = generate_cosmic_random_routes(content, image_rel=image_rel, params=params)
                write_cosmic_random_routes(
                    data, self.public_dir / "routes" / f"scene-{i + 1:02d}.routes.json")
                coverages.append(data["meta"]["maskCoverage"])
                continue
            if pen_brush:
                outline_end = round(d * self.cfg.drawing_outline_ratio)
                paint_start = outline_end + self.cfg.drawing_handoff_frames
                paint_end = round(d * self.cfg.drawing_paint_end_ratio)
                if paint_start >= paint_end:
                    raise SystemExit(f"scene-{i + 1:02d}: handoff 뒤 paint 타임라인이 비어 있음")
                op = RouteParams(
                    duration=d, draw_start=8, draw_end=outline_end,
                    pen_invisible_after=outline_end, seed=seed, analyze_scale=1.5,
                    contour_width=16, rdp_eps=1.4, max_len=300, min_route_len=8,
                    seal_width=28, seal_step=4, group_by_zone=True,
                )
                op.width, op.height = self.canvas
                outline_rel = f"{self.cfg.project_id}/bg/scene-{i + 1:02d}-outline.png"
                outline = generate_routes(
                    self.public_dir / "bg" / f"scene-{i + 1:02d}-outline-flat.png",
                    op, image_rel=outline_rel,
                )
                outline_path = self.public_dir / "routes" / f"scene-{i + 1:02d}-outline.routes.json"
                write_routes(outline, outline_path)
                paint_rel = f"{self.cfg.project_id}/bg/scene-{i + 1:02d}-color.png"
                paint = generate_fill_routes(
                    self.public_dir / "bg" / f"scene-{i + 1:02d}-color.png",
                    image_rel=paint_rel, duration=d, draw_start=paint_start, draw_end=paint_end,
                    seed=seed,
                )
                paint_path = self.public_dir / "routes" / f"scene-{i + 1:02d}-paint.routes.json"
                write_fill_routes(paint, paint_path)
                if paint["meta"]["coverage"] < 0.9999:
                    raise SystemExit(
                        f"scene-{i + 1:02d}: paint coverage {paint['meta']['coverage']:.6f} < 0.9999 "
                        f"(missingPixels={paint['meta']['missingPixels']})")
                coverages.append({"outline": outline["meta"]["coverage"],
                                  "paint": paint["meta"]["coverage"],
                                  "paintMissingPixels": paint["meta"]["missingPixels"]})
                continue
            if pen:
                params = pen_route_params(d, seed, draw_start=opening_draw_start)
                # 기본 pen은 잉크 알파만 리빌한다. preserveSource는 같은 펜 경로를
                # 사용하되 완료 마스크에서 원본 전체 이미지로 자연스럽게 복원한다.
                image_rel = f"{self.cfg.project_id}/bg/scene-{i + 1:02d}.png" \
                    if self.cfg.drawing_preserve_source \
                    else f"{self.cfg.project_id}/bg/scene-{i + 1:02d}-ink.png"
            else:
                params = RouteParams(
                    duration=d, draw_start=opening_draw_start, draw_end=round(d * 0.73),
                    pen_invisible_after=round(d * 0.76),
                    seed=seed, contour_width=40, seal_width=56, seal_step=20,
                )
                image_rel = f"{self.cfg.project_id}/bg/scene-{i + 1:02d}-content.png"
            params.width, params.height = self.canvas  # format 좌표계 전파 (youtube 는 기본값과 동일)
            data = generate_routes(content, params, image_rel=image_rel)
            if pen and self.cfg.drawing_preserve_source:
                data["meta"]["preserveSourceColor"] = True
            if pen and data["meta"]["coverage"] < 0.98:
                # full-bleed처럼 콘텐츠 밀도가 높은 씬은 기본 seal 간격으로 작은 구멍이 남을 수 있다.
                # 최종 bitmap 팝업으로 덮지 않고 route 자체를 촘촘하게 재생성한다.
                for seal_width, seal_step in ((28, 12), (36, 8)):
                    log.warning(
                        "scene-%02d: coverage %.4f < 0.98 — seal %d/%d 재시도",
                        i + 1, data["meta"]["coverage"], seal_width, seal_step,
                    )
                    params.seal_width = seal_width
                    params.seal_step = seal_step
                    data = generate_routes(content, params, image_rel=image_rel)
                    if data["meta"]["coverage"] >= 0.98:
                        break
            if pen and self.cfg.drawing_preserve_source:
                data["meta"]["preserveSourceColor"] = True
            write_routes(data, self.public_dir / "routes" / f"scene-{i + 1:02d}.routes.json")
            if pen:
                self._export_zone_assets(i, data)  # 존 크롭 + zones.json (Claude 매핑 계약)
            coverages.append(data["meta"]["coverage"])
        return {"coverages": coverages, "profile": self.cfg.drawing_profile}

    def _export_zone_assets(self, scene_idx: int, routes_data: dict) -> None:
        """존 크롭 PNG + zones.json 산출 — sync-map(수동/Claude 매핑) 작성용 계약."""
        from PIL import Image
        zones = routes_data.get("zones") or []
        out_dir = self.data_dir / "zones" / f"scene-{scene_idx + 1:02d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        ink = Image.open(self.public_dir / "bg" / f"scene-{scene_idx + 1:02d}-ink.png")
        pad = 12
        for z in zones:
            x0, y0, x1, y1 = z["bbox"]
            crop = ink.crop((max(0, x0 - pad), max(0, y0 - pad),
                             min(ink.width, x1 + pad), min(ink.height, y1 + pad)))
            crop.save(out_dir / f"zone-{z['zone']:02d}.png")
        (out_dir / "zones.json").write_text(json.dumps({
            "sceneId": f"scene-{scene_idx + 1:02d}",
            "image": routes_data["meta"]["image"],
            "zones": zones,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def stage_sync(self) -> dict:
        """내레이션 동기 드로잉 — pen + cue 있는 씬의 routes 를 cue 구간으로 리타이밍."""
        if self.cfg.drawing_profile == "pen-brush" and self.cfg.drawing_sync != "off":
            boundaries = []
            for i, scene in enumerate(self._scenes()):
                d = scene["durationInFrames"]
                cues = scene.get("cues") or []
                if cues:
                    # 실제 도구 교체(paint start)를 문장 경계에 놓고 handoff는 그 직전에 둔다.
                    paint_start = snap_pen_brush_boundary(d, cues, ratio=self.cfg.drawing_outline_ratio)
                    boundary = max(8 + 1, paint_start - self.cfg.drawing_handoff_frames)
                else:
                    boundary = d * self.cfg.drawing_outline_ratio
                    paint_start = boundary + self.cfg.drawing_handoff_frames
                paint_end = d * self.cfg.drawing_paint_end_ratio
                outline_path = self.public_dir / "routes" / f"scene-{i + 1:02d}-outline.routes.json"
                paint_path = self.public_dir / "routes" / f"scene-{i + 1:02d}-paint.routes.json"
                if not outline_path.is_file() or not paint_path.is_file():
                    raise SystemExit("pen-brush phase routes 없음 — `--from routes`로 다시 실행하세요")
                outline = retime_range(json.loads(outline_path.read_text()), 8, boundary, pen_tail=0)
                paint = retime_range(json.loads(paint_path.read_text()), paint_start, paint_end, pen_tail=6)
                write_routes(outline, outline_path)
                write_fill_routes(paint, paint_path)
                boundaries.append({"outlineEnd": boundary, "paintStart": paint_start, "paintEnd": paint_end})
            return {"profile": "pen-brush", "boundaries": boundaries}
        if self.cfg.drawing_profile != "pen" or self.cfg.drawing_sync == "off":
            log.info("sync: profile=%s sync=%s — 해당 없음 (routes 불변)",
                     self.cfg.drawing_profile, self.cfg.drawing_sync)
            return {"skippedReason": f"{self.cfg.drawing_profile}/{self.cfg.drawing_sync}"}
        sync_map_path = self.data_dir / "sync-map.json"
        sync_maps: dict[str, dict] = {}
        if sync_map_path.is_file():
            raw = json.loads(sync_map_path.read_text(encoding="utf-8"))
            sync_maps = {s["sceneId"]: s for s in raw.get("scenes", [])}
            log.info("sync: sync-map.json 사용 (%d씬)", len(sync_maps))
        synced = []
        for i, scene in enumerate(self._scenes()):
            scene_id = f"scene-{i + 1:02d}"
            cues = scene.get("cues") or []
            zones_path = self.data_dir / "zones" / scene_id / "zones.json"
            routes_path = self.public_dir / "routes" / f"{scene_id}.routes.json"
            if not cues:
                log.info("sync: %s cue 0개 — 자동 off", scene_id)
                synced.append(None)
                continue
            if not zones_path.is_file():
                # sync 기능 도입 전에 빌드된 구 캐시 — routes 재생성으로 zone 에셋부터 만들어야 함
                raise SystemExit(
                    f"sync: {zones_path} 없음 — sync 기능 이전의 캐시입니다. "
                    f"`--from routes`로 재실행해 zone 에셋을 생성하세요.")
            zones = json.loads(zones_path.read_text(encoding="utf-8"))["zones"]
            data = json.loads(routes_path.read_text(encoding="utf-8"))
            sync_cues = cues
            if self.cfg.drawing_preserve_source:
                # 원본 복원(develop)과 완성 화면 감상 뒤 outro가 오도록 마지막
                # 드로잉을 씬 끝 60프레임 전에 끝낸다. 실제 음성/자막 시간은 바꾸지 않는다.
                sync_cues = [dict(cue) for cue in cues]
                completion_cap = max(
                    float(sync_cues[-1]["from"]) + 1,
                    float(scene["durationInFrames"]) - 60,
                )
                sync_cues[-1]["to"] = min(float(sync_cues[-1]["to"]), completion_cap)
            out = apply_sync(data, zones, sync_cues, sync_map=sync_maps.get(scene_id))
            if self.cfg.drawing_preserve_source:
                out["meta"]["completionTailFrames"] = round(
                    float(scene["durationInFrames"]) - float(out["meta"]["drawEnd"]), 2)
            write_routes(out, routes_path)
            synced.append(out["meta"].get("zoneCueAssignment"))
        return {"synced": synced, "syncMap": bool(sync_maps)}

    def stage_layout(self) -> dict:
        if self.cfg.widgets != "authored" or not self.cfg.authored_widgets:
            log.info("layout: widgets=%s — 배치/검증 대상 없음 (auto 는 보류)", self.cfg.widgets)
            return {"widgets": self.cfg.widgets}
        # authored 위젯 겹침 검증 (첫 씬 배경 기준, hard-fail)
        scenes = self._scenes()
        content = self.public_dir / "bg" / "scene-01-content.png"
        res = validate_layout(self.cfg.authored_widgets, has_cues=bool(scenes[0].get("cues")),
                              image_path=content if content.is_file() else None,
                              canvas=self.canvas)
        if not res.ok:
            raise SystemExit("레이아웃 검증 실패(hard-fail): " + "; ".join(res.fails))
        return {"widgets": "authored", "count": len(self.cfg.authored_widgets), "warns": res.warns}

    def stage_props(self) -> dict:
        scenes_meta = self._scenes()
        if self.cfg.drawing_profile == FULL_COLOR_MOTION_PROFILE:
            props = bv_motion.build_motion_props(
                self.cfg,
                scenes_meta,
                shorts_subtitle_style=SHORTS_SUBTITLE_STYLE,
            )
            bv_motion.write_motion_props(props, self.props_json)
            return {
                "props": self.runtime.relative(self.props_json),
                "profile": FULL_COLOR_MOTION_PROFILE,
                "brushRevealScenes": [scene["id"] for scene in props["scenes"]
                                      if scene["reveal"]["mode"] == "brush"],
            }
        scene_props = []
        completion_timings = []
        schema_scene_props = load_schema()["properties"]["scenes"]["items"]["properties"]
        shorts_ambient = self.cfg.fmt == "shorts" and self.cfg.mode == "ambient"
        full_bleed = self.cfg.drawing_profile in FULL_BLEED_PROFILES
        coordinate_scale = self.canvas[0] / 1920
        for i, sm in enumerate(scenes_meta):
            kwargs: dict = {}
            if self.cfg.overlays == "none":
                # 프롬프트 기반 아트 필름은 cue를 타이밍/QA 데이터로만 사용한다.
                # 원본 인물·사물·서사를 가리는 제목/자막/카드 오버레이를 렌더하지 않는다.
                kwargs["captionsVisible"] = False
            if not full_bleed and self.cfg.drawing_profile in {"brush", "cosmic-random-brush"}:
                # 10초 장면 내내 1.2% 범위의 느린 카메라 이동만 적용한다. opacity=0 이므로
                # 효과 파티클은 추가하지 않고 마스크·원본 프레임만 함께 움직인다.
                kwargs["naturalEffects"] = {
                    "kind": "mist", "opacity": 0, "parallaxScale": 1.012,
                    "seed": self.cfg.seed + i,
                }
            if shorts_ambient:
                # 세로 연출 프리셋: 자막 세이프존 + 씬 배경 동조 강조색 (사용자 명시 값 우선)
                style = {**SHORTS_SUBTITLE_STYLE, **self.cfg.subtitle_style}
                # min_sat 완화: preset 수채 워시는 채도가 낮아 기본(30)로는 잉크 폴백만 나옴
                style.setdefault("highlightColor", title_color(
                    self.public_dir / "bg" / f"scene-{i + 1:02d}-content.png", min_sat=14))
                kwargs["subtitle_style"] = style
                kwargs["outroFadeFrames"] = SHORTS_OUTRO_FADE_FRAMES  # 씬 전환 페이드
                if i == 0:
                    kwargs.update(SHORTS_FIRST_PREWASH)  # 훅: 첫 씬 짧은 프리워시
                if i == len(scenes_meta) - 1:
                    kwargs["outroWashOpacity"] = SHORTS_LOOP_WASH_OPACITY  # 루프: 순백 수렴
            if i == 0 and self.cfg.title and self.cfg.overlays != "none":
                accent = title_color(self.public_dir / "bg" / "scene-01-content.png")
                kwargs["top_title"] = {"lines": [self.cfg.title], "enterAt": 12, "accent": accent}
                if self.cfg.fmt == "shorts":
                    kwargs["top_title"]["y"] = 140  # 세로 상단 세이프존(y ≥ 120) 준수
            if self.cfg.widgets == "authored" and self.cfg.authored_widgets and i == 0:
                if "widgets" in schema_scene_props:
                    kwargs["widgets"] = self.cfg.authored_widgets
                else:  # 위젯 워크스트림 통합 전 — 스키마에 없으면 제외
                    log.warning("스키마에 scenes[].widgets 없음 — authored 위젯은 props 에서 제외(통합 대기)")
            if full_bleed:
                # 원본 프레임의 구도와 캐릭터를 가리지 않는다. cue는 매니페스트/QA용으로만 유지한다.
                kwargs.pop("top_title", None)
                image = f"{self.cfg.project_id}/bg/scene-{i + 1:02d}-content.png"
                presentation_kwargs = {
                    "presentation": self.cfg.drawing_profile,
                    "image": image,
                    "captionsVisible": False,
                }
                if i > 0:
                    presentation_kwargs["previousImage"] = (
                        f"{self.cfg.project_id}/bg/scene-{i:02d}-content.png")
                built_scene = build_scene(
                    f"scene-{i + 1:02d}", None, sm["durationInFrames"],
                    cues=sm.get("cues") or None,
                    brush_dynamics={"drawSpeedScale": 1.0, "touchScale": 1.0,
                                    "touchJitter": 0, "randomizeOrder": False,
                                    "randomReverse": False, "seed": self.cfg.seed + i},
                    **presentation_kwargs,
                    **kwargs,
                )
                scene_props.append(built_scene)
                continue
            pen = self.cfg.drawing_profile == "pen"
            pen_brush = self.cfg.drawing_profile == "pen-brush"
            cosmic = self.cfg.drawing_profile == "cosmic-random-brush"
            if pen_brush:
                outline_path = self.public_dir / "routes" / f"scene-{i + 1:02d}-outline.routes.json"
                paint_path = self.public_dir / "routes" / f"scene-{i + 1:02d}-paint.routes.json"
                if not outline_path.is_file() or not paint_path.is_file():
                    raise SystemExit("pen-brush phase routes 없음 — `--from routes`로 다시 실행하세요")
                outline_meta = json.loads(outline_path.read_text(encoding="utf-8"))["meta"]
                paint_meta = json.loads(paint_path.read_text(encoding="utf-8"))["meta"]
                fade_to = round(float(paint_meta["drawEnd"]))
                fade_from = max(round(float(paint_meta["drawStart"])), fade_to - 24)
                kwargs.update(PEN_BRUSH_SCENE_PRESET)
                kwargs["viewportScale"] = self.cfg.drawing_viewport_scale
                kwargs["drawingPhases"] = [
                    {"kind": "outline", "routes": f"{self.cfg.project_id}/routes/scene-{i + 1:02d}-outline.routes.json",
                     "cursor": scale_brush(PEN_BRUSH, coordinate_scale), "zIndex": 20, "edgeFeather": 0,
                     "fadeOutFrom": fade_from, "fadeOutTo": fade_to},
                    {"kind": "paint", "routes": f"{self.cfg.project_id}/routes/scene-{i + 1:02d}-paint.routes.json",
                     "cursor": scale_brush(PAINT_BRUSH, coordinate_scale), "zIndex": 10, "edgeFeather": 2},
                ]
            if pen:
                kwargs.update(PEN_SCENE_PRESET)  # 즉시 또렷 + 선화 feather + prewash 없음
                if self.cfg.drawing_preserve_source:
                    # 색면·그림자 전체 복원은 안티앨리어싱 마감보다 넓은 페이드가 필요하다.
                    kwargs["developFrames"] = 18
                    kwargs["colorSettleFrames"] = 0
                    # 원본 전체를 희미한 가이드로 먼저 보여주고 펜이 그린 구간만
                    # 선명하게 만든다. 밝은 카드 면이 8초 동안 빈 종이로 보이는 현상을 막는다.
                    kwargs["previewOpacity"] = 0.32
                elif self._uses_first_scene_blurred_poster(i):
                    kwargs.update(PEN_FIRST_SCENE_BLURRED_POSTER)
                    kwargs["prewashImage"] = self._first_scene_poster_image()
                dynamics = {**PEN_BRUSH_DYNAMICS, "seed": self.cfg.seed + i}
            elif cosmic:
                dynamics = {**PEN_BRUSH_DYNAMICS, "seed": self.cfg.seed + i}
                kwargs.update({
                    "faint": 1.0,
                    "edgeFeather": 0,
                    "linearDraw": True,
                    "completionMode": "masked-hold",
                    "prewashOpacity": 0,
                    "prewashFrames": 0,
                    "prewashHoldFrames": 0,
                    "outroFadeFrames": 0,
                })
                cue_title = (sm.get("cues") or [{}])[0].get("text")
                scene_title = self.cfg.title if i == 0 and self.cfg.title else cue_title
                if scene_title and self.cfg.overlays != "none":
                    kwargs["top_title"] = {
                        "kicker": "FREE BRUSH STUDY",
                        "lines": [scene_title],
                        "x": 68, "y": 54, "width": 760,
                        "enterAt": 7, "accent": "#86e8ff", "color": "#eef8ff",
                        "fontSize": 36, "kickerFontSize": 14,
                    }
            else:
                dynamics = {"drawSpeedScale": 1.0, "touchScale": 1.45, "touchJitter": 0.22,
                            "randomizeOrder": True, "randomReverse": True, "seed": self.cfg.seed + i}
                # 일반 가로 brush만 무펄스 완료 프리셋을 기본 적용한다. shorts와 다른
                # drawing profile은 각자의 승인 프리셋을 그대로 유지한다.
                if self.cfg.drawing_profile == "brush" and self.cfg.fmt == "youtube":
                    route_path = self.public_dir / "routes" / f"scene-{i + 1:02d}.routes.json"
                    route_data = json.loads(route_path.read_text(encoding="utf-8"))
                    strokes = route_data.get("strokes") or []
                    last_end = max((float(s.get("end", 0)) for s in strokes), default=0.0)
                    preset = dict(BRUSH_NO_PULSE_PRESET)
                    fitted = fit_brush_completion_timing(
                        sm["durationInFrames"], last_end,
                        outro_fade_frames=int(preset["outroFadeFrames"]),
                    )
                    preset["developFrames"] = fitted["developFrames"]
                    preset["colorSettleFrames"] = fitted["colorSettleFrames"]
                    kwargs.update(preset)
                if self._uses_first_scene_blurred_poster(i):
                    kwargs.update(BRUSH_FIRST_SCENE_BLURRED_POSTER)
                    kwargs["prewashImage"] = self._first_scene_poster_image()
            built_scene = build_scene(
                f"scene-{i + 1:02d}", None if pen_brush else f"{self.cfg.project_id}/routes/scene-{i + 1:02d}.routes.json",
                sm["durationInFrames"], cues=None if cosmic else (sm.get("cues") or None),
                brush_dynamics=dynamics,
                **kwargs,
            )
            if self.cfg.drawing_profile == "brush" and self.cfg.fmt == "youtube":
                timing = validate_brush_completion_timing(built_scene, strokes)
                completion_timings.append({"sceneId": built_scene["id"], **timing})
            scene_props.append(built_scene)
        pen = self.cfg.drawing_profile == "pen"
        pen_brush = self.cfg.drawing_profile == "pen-brush"
        cosmic = self.cfg.drawing_profile == "cosmic-random-brush"
        props = build_props(self.cfg.project_id, scene_props, title=self.cfg.title,
                            fmt=self.cfg.fmt, audio=None,  # 오디오는 렌더 후 ffmpeg mux
                            paper="#090b10" if full_bleed else ("#01020d" if cosmic else
                                  (PEN_PAPER_HEX if (pen or pen_brush) else "#fbfaf6")),
                            brush=scale_brush(PEN_BRUSH, coordinate_scale) if pen
                            else scale_brush(default_brush(), coordinate_scale))
        write_props(props, self.props_json)
        return {"props": self.runtime.relative(self.props_json), "profile": self.cfg.drawing_profile,
                "completionTimings": completion_timings}

    def stage_render(self) -> dict:
        composition = resolve_composition(self.cfg)
        total_frames = sum(s["durationInFrames"] for s in self._scenes())
        if total_frames >= 18_000:
            chunk_frames = 3_000
            segments = [(start, min(start + chunk_frames - 1, total_frames - 1))
                        for start in range(0, total_frames, chunk_frames)]
            # 입력별 디렉터리로 분리해 같은 입력의 중단 재개만 허용한다. 길이만 같은
            # 과거 청크를 재사용하면 이미지/routes 변경 뒤에도 이전 영상이 남는다.
            work_dir = self.data_dir / "render-chunks" / self._render_input_signature()
            bv_render.render_segments(
                self.props_json, self.video_only, segments,
                composition=composition, concurrency=UHD_RENDER_CONCURRENCY, work_dir=work_dir,
                repo_root=self.runtime.engine_root, public_root=self.runtime.public_root,
            )
            return {"video": self.runtime.relative(self.video_only),
                    "composition": composition, "segmented": True,
                    "segmentCount": len(segments), "segmentFrames": chunk_frames}
        # 장편과 동일한 내부 워커 수를 사용하되, 프로젝트 렌더 자체는 큐에서 하나만 실행한다.
        bv_render.render(self.props_json, self.video_only, composition=composition,
                         concurrency=UHD_RENDER_CONCURRENCY if self.cfg.render_resolution == "uhd" else 4,
                         repo_root=self.runtime.engine_root, public_root=self.runtime.public_root)
        return {"video": self.runtime.relative(self.video_only),
                "composition": composition, "segmented": False}

    def stage_mix(self) -> dict:
        """BGM 준비 + 내레이션 덕킹 → 최종 master.wav. 구 프로젝트는 원본 오디오를 보존."""
        total_sec = sum(s["durationInFrames"] for s in self._scenes()) / FPS
        voice = self._voice_path()
        audio_dir = self.data_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        signature = self._mix_signature()

        bgm_cfg = self.cfg.bgm
        auto_selected = False
        generated_candidate = None

        # 정책: 대사(음성/TTS)도 없고 bgm 블록도 없으면 Stable Audio 피아노를
        # 먼저 시도한다. 런타임/길이/생성 문제가 있으면 기존 catalog와 synth를 유지한다.
        if bgm_cfg is None and voice is None:
            try:
                bgm_cfg = BgmConfig(mode="piano-auto")
                generated_candidate = bv_piano_auto.build_candidate(
                    self.cfg, total_sec, bgm_cfg,
                    output_root=self.runtime.output_root / "original-audio" / "piano-bgm",
                    projects_root=self.runtime.workspace_root / "projects" / "piano-bgm",
                )
                auto_selected = True
                log.info("auto-bgm: 대사 없음 → Stable Audio piano-auto [%s]", generated_candidate["status"])
            except bv_piano_auto.PianoAutoBgmError as exc:
                log.warning("auto-bgm: Stable Audio 후보 미준비 — 기존 catalog/synth 폴백 (%s)", exc)
                bgm_cfg = None

        # Stable Audio가 없는 환경 또는 120초 초과 영상은 기존 결정적 catalog를 유지한다.
        if bgm_cfg is None and voice is None:
            candidate = bv_bgm.select_auto_bgm(
                profile=self.cfg.drawing_profile, fmt=self.cfg.fmt, duration_sec=total_sec)
            try:
                self._bgm_assets = bv_bgm.preflight_assets(
                    candidate, distribution=self.cfg.fmt, repo_root=self.runtime.engine_root)
                bgm_cfg = candidate
                auto_selected = True
                log.info("auto-bgm: 기존 catalog fallback → %s [%s]",
                         candidate.asset_id or ", ".join(candidate.asset_ids), candidate.mode)
            except bv_bgm.BgmAssetError as exc:
                log.warning("auto-bgm: 로컬 자산 미준비 — synth BGM 폴백 (%s)", exc)

        # bgm 블록 없음(구 프로젝트) 또는 자동선택 폴백: 앰비언트 합성/음성 직접 mux를 그대로 유지한다.
        if bgm_cfg is None:
            if self.cfg.mode == "ambient":
                legacy = self.data_dir / "bgm.wav"
                bv_audio.synth_ambient_bgm(legacy, total_sec, seed=self.cfg.seed)
                return {"audio": self.runtime.relative(legacy), "mode": "legacy-synth", "signature": signature}
            return {"audio": self.runtime.relative(voice) if voice else None, "mode": "legacy-voice", "signature": signature}

        if bgm_cfg.mode == "off":
            if voice is None:
                # H.264/AAC MP4 납품 계약은 유지하되 BGM을 삽입하지 않는다. 영상만
                # 복사하면 오디오 스트림 자체가 사라지므로, 명시적으로 무음 AAC로 mux한다.
                master = bv_audio.create_silent_audio(audio_dir / "silent.wav", total_sec)
                report = {
                    "projectId": self.cfg.project_id,
                    "durationSec": total_sec,
                    "mode": "off",
                    "bgm": None,
                    "voice": None,
                    "settings": {"bgmEnabled": False, "silentAudioTrack": True},
                }
                mix_report = bv_mix.write_mix_report(audio_dir / "mix-report.json", report)
                return {"audio": self.runtime.relative(master), "mode": "off",
                        "report": self.runtime.relative(mix_report),
                        "silentAudioTrack": True, "signature": signature}
            # BGM을 끈 영상도 원본 음성을 그대로 mux하지 않고 전달 규격(-16 LUFS,
            # 48kHz stereo, 목표 길이 고정)으로 정규화한다.
            master, voice_input = bv_mix.normalize_voice(
                voice, audio_dir / "master.wav", duration_sec=total_sec)
            voice_output = bv_mix.measure_loudness(master, duration_sec=total_sec)
            report = {
                "projectId": self.cfg.project_id,
                "durationSec": total_sec,
                "mode": "off",
                "bgm": None,
                "voice": {
                    "voiceInput": voice_input,
                    "ducking": {"enabled": False, "reason": "bgm-off"},
                    "output": voice_output,
                },
                "settings": {"bgmEnabled": False},
            }
            mix_report = bv_mix.write_mix_report(audio_dir / "mix-report.json", report)
            return {"audio": self.runtime.relative(master), "mode": "off",
                    "report": self.runtime.relative(mix_report),
                    "signature": signature}
        if total_sec > 600 and bgm_cfg.mode == "asset":
            log.warning("10분 초과 영상에 단일 BGM 1곡 사용 — 2~3곡 playlist + crossfade 권장")

        if bgm_cfg.mode == "piano-auto":
            if generated_candidate is None:
                try:
                    generated_candidate = bv_piano_auto.build_candidate(
                        self.cfg, total_sec, bgm_cfg,
                        output_root=self.runtime.output_root / "original-audio" / "piano-bgm",
                        projects_root=self.runtime.workspace_root / "projects" / "piano-bgm",
                    )
                except bv_piano_auto.PianoAutoBgmError as exc:
                    raise ValueError(f"명시적 bgm.mode=piano-auto 생성 실패: {exc}") from exc
            asset_paths = [Path(generated_candidate["source"])]
        elif bgm_cfg.mode == "synth":
            raw_bgm = audio_dir / "synth-raw.wav"
            bv_audio.synth_ambient_bgm(raw_bgm, total_sec, seed=self.cfg.seed)
            asset_paths = [raw_bgm]
        else:
            asset_paths = [Path(a["resolvedPath"]) for a in self._bgm_assets]
            bv_bgm.write_license_manifest(
                self.data_dir / "licenses" / "bgm-manifest.json",
                self.cfg.project_id, bgm_cfg, self._bgm_assets,
                distribution=self.cfg.fmt,
            )

        has_voice = voice is not None
        gain_db = bgm_cfg.gain_db if bgm_cfg.gain_db is not None else (3.0 if has_voice else 5.0)
        bgm_master, bgm_report = bv_mix.prepare_bgm(
            asset_paths, audio_dir / "bgm-master.wav", duration_sec=total_sec,
            work_dir=audio_dir / "work-bgm", gain_db=gain_db,
            fade_in_sec=bgm_cfg.fade_in_sec, fade_out_sec=bgm_cfg.fade_out_sec,
            crossfade_sec=bgm_cfg.crossfade_sec,
            source_start_sec=bgm_cfg.source_start_sec,
        )
        report = {"projectId": self.cfg.project_id, "durationSec": total_sec,
                  "mode": bgm_cfg.mode, "bgm": bgm_report, "voice": None,
                  "settings": {"gainDb": gain_db, "sourceStartSec": bgm_cfg.source_start_sec,
                               "fadeInSec": bgm_cfg.fade_in_sec,
                               "fadeOutSec": bgm_cfg.fade_out_sec,
                               "crossfadeSec": bgm_cfg.crossfade_sec}}
        if generated_candidate is not None:
            report["generatedCandidate"] = generated_candidate
        if has_voice:
            duck_enabled = bgm_cfg.ducking_enabled if bgm_cfg.ducking_enabled is not None else True
            master, voice_report = bv_mix.mix_voice_and_bgm(
                voice, bgm_master, audio_dir / "master.wav", duration_sec=total_sec,
                ducking_enabled=duck_enabled, ducking_amount_db=bgm_cfg.ducking_amount_db,
                attack_ms=bgm_cfg.ducking_attack_ms, release_ms=bgm_cfg.ducking_release_ms,
                work_dir=audio_dir / "work-mix",
            )
            report["voice"] = voice_report
        else:
            master = bv_mix.copy_master(bgm_master, audio_dir / "master.wav")
        mix_report = bv_mix.write_mix_report(audio_dir / "mix-report.json", report)
        return {"audio": self.runtime.relative(master), "mode": bgm_cfg.mode, "gainDb": gain_db,
                "autoSelected": auto_selected,
                "generatedCandidate": generated_candidate,
                "assetIds": list(bv_bgm.selected_asset_ids(bgm_cfg)),
                "report": self.runtime.relative(mix_report), "signature": signature}

    def stage_mux(self) -> dict:
        audio_value = self.ledger.payload("mix").get("audio")
        audio = self.runtime.resolve(audio_value) if audio_value else None
        # pen-brush 동화는 색칠 완료 첫 장면을 MP4 cover art에도 첨부한다. 카드형 플레이어는
        # attached_pic을 무시할 수 있으므로 실제 v:0 0프레임 포스터는 DrawingPhaseLayer가 담당한다.
        cover = self.public_dir / "bg" / "scene-01-content.png"
        cover = cover if self.cfg.drawing_profile == "pen-brush" and cover.is_file() else None
        if audio is None:
            self.output.parent.mkdir(parents=True, exist_ok=True)
            if cover is None:
                log.info("mux: 오디오 없음 — 영상만 복사")
                shutil.copyfile(self.video_only, self.output)
            else:
                bv_render.attach_cover_art(self.video_only, cover, self.output)
            return {"audio": None, "coverArt": self.runtime.relative(cover) if cover else None}
        bv_render.mux_audio(self.video_only, audio, self.output)
        if cover is not None:
            covered_output = self.output.with_name(f".{self.output.stem}.cover.mp4")
            try:
                bv_render.attach_cover_art(self.output, cover, covered_output)
                covered_output.replace(self.output)
            finally:
                covered_output.unlink(missing_ok=True)
        return {"audio": self.runtime.relative(audio), "mixMode": self.ledger.payload("mix").get("mode"),
                "coverArt": self.runtime.relative(cover) if cover else None}

    def stage_qa(self) -> dict:
        # 대량 프로젝트는 상위 오케스트레이터가 전수 QA를 한 번 수행할 수 있다.
        # 명시적 marker가 있을 때만 씬별 Remotion still 3회(고비용)를 생략한다.
        if (self.data_dir / ".skip-internal-qa").is_file():
            log.info("qa: 외부 전수 QA marker 감지 — 씬별 still 캡처 생략")
            return {"skippedReason": "external-batch-qa"}
        frames, labels = [], {}
        render_props = json.loads(self.props_json.read_text(encoding="utf-8"))
        props_scenes = render_props.get("scenes") or []
        completion_timings = []
        offset = 0
        for i, sm in enumerate(self._scenes()):
            d = sm["durationInFrames"]
            if self.cfg.drawing_profile == FULL_COLOR_MOTION_PROFILE:
                spec = self.cfg.motion.scene_for(i) if self.cfg.motion else None
                local = (("reveal-end", max(1, min(d - 8, spec.reveal_frames - 1))),
                         ("motion-mid", d // 2), ("pre-transition", d - 8)) \
                    if spec and spec.reveal == "brush" else \
                    (("opening", 12), ("motion-mid", d // 2), ("pre-transition", d - 8))
            elif self.cfg.drawing_profile == "pen-brush":
                om = json.loads((self.public_dir / "routes" / f"scene-{i + 1:02d}-outline.routes.json").read_text())["meta"]
                pm = json.loads((self.public_dir / "routes" / f"scene-{i + 1:02d}-paint.routes.json").read_text())["meta"]
                local = (("start", 6), ("outline-mid", round((om["drawStart"] + om["drawEnd"]) / 2)),
                         ("handoff", round((om["drawEnd"] + pm["drawStart"]) / 2)),
                         ("paint-mid", round((pm["drawStart"] + pm["drawEnd"]) / 2)), ("final", d - 8))
            elif self.cfg.drawing_profile == "cosmic-random-brush":
                if self.cfg.ambient_scenes == 60:
                    local = (("first-touch", 51), ("base-pass", 174), ("brush-complete", 209))
                else:
                    local = (("opening", 21), ("first-touch", 51), ("random-pass", 102),
                             ("base-pass", 174), ("brush-complete", 209), ("outro", 291))
            elif (self.cfg.drawing_profile == "brush" and self.cfg.fmt == "youtube"
                  and props_scenes[i].get("completionMode") == "integrated-develop"):
                route_data = json.loads((self.public_dir / "routes" / f"scene-{i + 1:02d}.routes.json")
                                        .read_text(encoding="utf-8"))
                timing = bv_qa.completion_timing(props_scenes[i], route_data)
                samples = bv_qa.completion_sample_frames(timing)
                local = tuple(samples)
                completion_timings.append({**timing, "offset": offset,
                                           "samples": [{"label": label, "frame": offset + frame}
                                                       for label, frame in samples]})
            else:
                local = (("start", 12), ("mid", d // 2), ("end", d - 8))
            for tag, local_frame in local:
                f = offset + local_frame
                frames.append(f)
                labels[f] = f"scene-{i + 1:02d} {tag}"
            offset += d
        composition = resolve_composition(self.cfg)
        qa_dir = self.data_dir / "qa"
        # 완성 MP4가 이미 존재하므로 장편 pen-brush도 최종 인코딩 결과에서 직접
        # 샘플링한다. Remotion still을 프레임마다 재번들링하면 60씬 기준 수십 분이
        # 들고, 실제 전달 파일과 다른 렌더 경로를 검증하게 된다.
        if self.cfg.drawing_profile == "pen-brush" \
                or (self.cfg.drawing_profile == "cosmic-random-brush" and self.cfg.ambient_scenes == 60) \
                or self.cfg.drawing_profile == FULL_COLOR_MOTION_PROFILE \
                or self.cfg.drawing_profile in FULL_BLEED_PROFILES \
                or completion_timings:
            entries = bv_qa.capture_video_frames(self.output, frames, qa_dir,
                                                 labels=labels, fps=30.0)
        else:
            entries = bv_qa.capture_frames(self.props_json, frames, qa_dir,
                                           composition=composition, labels=labels,
                                           repo_root=self.runtime.engine_root,
                                           public_root=self.runtime.public_root)
        bv_qa.write_manifest(entries, qa_dir, project_id=self.cfg.project_id,
                             props=self.runtime.relative(self.props_json))
        sheet = bv_qa.contact_sheet(
            qa_dir, cols=6 if self.cfg.ambient_scenes == 60 else 3,
            thumb_w=300 if self.cfg.ambient_scenes == 60 else 460)
        gallery = bv_qa.build_gallery(self.props_json, qa_dir)  # 씬 갤러리(카드뷰)
        payload = {"captures": len(entries), "contactSheet": self.runtime.relative(sheet),
                   "gallery": self.runtime.relative(gallery)}
        if completion_timings:
            report = bv_qa.write_completion_report(
                qa_dir / "completion-report.json", timings=completion_timings,
                entries=entries, qa_dir=qa_dir)
            payload["completionReport"] = self.runtime.relative(qa_dir / "completion-report.json")
            if not report["summary"]["pass"]:
                failed = [s["sceneId"] for s in report["scenes"] if not s["pass"]]
                raise SystemExit("brush 완료 구간 QA 실패 — " + ", ".join(failed))
        if self.cfg.drawing_profile == "pen-brush":
            report_scenes = []
            for i, _ in enumerate(self._scenes()):
                outline = json.loads((self.public_dir / "routes" / f"scene-{i + 1:02d}-outline.routes.json").read_text())["meta"]
                paint = json.loads((self.public_dir / "routes" / f"scene-{i + 1:02d}-paint.routes.json").read_text())["meta"]
                outline_asset = self.public_dir / "bg" / f"scene-{i + 1:02d}-outline.png"
                # fullBleed는 색칠 알파를 의도적으로 캔버스 전체로 확장한다. 원본 RGB의
                # 종이/콘텐츠 경계로 굵기를 재추정하면 실제 선 변화 없이 false delta가 난다.
                # 이 모드에서는 화면 확대 전후 모두 최상단 neutral outline 알파가 선의 정본이다.
                source_thickness = alpha_line_thickness(outline_asset) if self.cfg.drawing_full_bleed                     else estimate_line_thickness(self.public_dir / "bg" / f"scene-{i + 1:02d}.png")
                # 최종 프레임의 선은 color RGBA가 아니라 그 위에 놓이는 neutral outline RGBA가
                # 보존한다. 투명 color의 종이 재합성으로 선 굵기를 재측정하면 한지 질감에 의해
                # 오차가 생기므로, 실제 최상단 outline 레이어를 계측한다.
                final_thickness = alpha_line_thickness(outline_asset)
                delta_pct = (final_thickness - source_thickness) / max(1e-9, source_thickness) * 100
                report_scenes.append({"sceneId": f"scene-{i + 1:02d}", "outline": outline, "paint": paint,
                                      "lineThickness": source_thickness,
                                      "finalLineThickness": final_thickness,
                                      "finalLineThicknessDeltaPct": delta_pct})
            report = bv_qa.write_pen_brush_report(qa_dir / "pen-brush-report.json",
                                                   project_id=self.cfg.project_id, scenes=report_scenes)
            if not report["pass"]:
                raise SystemExit("pen-brush QA 수치 게이트 실패 — qa/pen-brush-report.json 확인")
            payload["penBrushReport"] = self.runtime.relative(qa_dir / "pen-brush-report.json")
        if self.cfg.drawing_profile == "cosmic-random-brush":
            report_scenes = []
            for i, _ in enumerate(self._scenes()):
                meta = json.loads((self.public_dir / "routes" / f"scene-{i + 1:02d}.routes.json").read_text())["meta"]
                report_scenes.append({"sceneId": f"scene-{i + 1:02d}", "meta": meta})
            report = bv_qa.write_cosmic_random_brush_report(
                qa_dir / "cosmic-random-brush-report.json",
                project_id=self.cfg.project_id, scenes=report_scenes)
            if not report["pass"]:
                raise SystemExit("cosmic-random-brush QA 수치 게이트 실패 — qa/cosmic-random-brush-report.json 확인")
            payload["cosmicRandomBrushReport"] = self.runtime.relative(
                qa_dir / "cosmic-random-brush-report.json")
        if self.cfg.drawing_profile in FULL_BLEED_PROFILES:
            report = bv_qa.write_full_bleed_report(
                qa_dir / "full-bleed-report.json",
                project_id=self.cfg.project_id,
                profile=self.cfg.drawing_profile,
                scenes=props_scenes,
                expected_scene_count=len(self._scenes()),
            )
            if not report["pass"]:
                raise SystemExit("full-bleed QA 수치 게이트 실패 — qa/full-bleed-report.json 확인")
            payload["fullBleedReport"] = self.runtime.relative(qa_dir / "full-bleed-report.json")
        return payload


def resolved_audit_artifacts(pipe: Pipeline) -> tuple[Path | None, Path | None]:
    """명시/자동 선택 구분 없이 실제 mix 산출물에서 감사 입력을 결정한다."""
    mix_payload = pipe.ledger.payload("mix")
    license_path = pipe.data_dir / "licenses" / "bgm-manifest.json"
    report_value = mix_payload.get("report")
    runtime = getattr(pipe, "runtime", None)
    report_path = runtime.resolve(report_value) if runtime and report_value else \
        (Path(report_value) if report_value else None)
    if mix_payload.get("mode") == "piano-auto":
        runtime_output = runtime.output_root if runtime else REPO_ROOT / "output"
        generated_path = runtime_output / "original-audio" / "piano-bgm" / pipe.cfg.project_id / "generated-bgm-manifest.json"
        license_path = generated_path if generated_path.is_file() else None
    uses_licensed_bgm = mix_payload.get("mode") in {"asset", "playlist", "piano-auto"}
    return (license_path if uses_licensed_bgm and license_path.is_file() else None,
            report_path if report_path and report_path.is_file() else None)


def main() -> None:
    ap = argparse.ArgumentParser(description="project.yaml → 완성 mp4 (스테이지 캐시 + --from 재개)")
    ap.add_argument("project_yaml", help="project.yaml 경로")
    ap.add_argument("--from", dest="from_stage", default=None,
                    help=f"이 스테이지부터 다시 실행 {STAGES}")
    ap.add_argument("--audit", action="store_true",
                    help="빌드 완료 후 video-auditor 자동 검수 (FAIL이면 exit 1)")
    ap.add_argument("--preflight", action="store_true",
                    help="렌더 전에 의존성·4K 원본·BGM 계약만 검사하고 종료")
    ap.add_argument("--final", action="store_true",
                    help="최종 납품 계약(600초 UHD/strict licensed BGM)을 강제")
    ap.add_argument("--verify-sources", action="store_true",
                    help="source manifest의 모든 PNG SHA-256을 재계산 (최종 큐 직전 권장)")
    ap.add_argument("--workspace", default=None,
                    help="격리 job workspace 경로. 생략하면 기존 engine-local CLI 모드")
    ap.add_argument("--events-jsonl", default=None,
                    help="workspace 내부 구조화 stage event JSONL 경로")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname).1s %(name)s: %(message)s")
    try:
        runtime = RuntimeContext.create(REPO_ROOT, a.workspace) if a.workspace \
            else RuntimeContext.standalone(REPO_ROOT)
        for key, value in runtime.capability_environment().items():
            os.environ.setdefault(key, value)
        if a.workspace:
            runtime.validate_project_source(a.project_yaml)
        cfg = load_project(a.project_yaml)
        if a.workspace:
            runtime.validate_project_inputs(cfg, a.project_yaml)
        if a.events_jsonl and not a.workspace:
            raise RuntimePathError("--events-jsonl requires --workspace")
        events = StageEventWriter(a.events_jsonl, runtime) if a.events_jsonl else None
    except RuntimePathError as exc:
        ap.error(str(exc))
    log.info("project=%s mode=%s format=%s", cfg.project_id, cfg.mode, cfg.fmt)
    if a.preflight or a.final:
        if events:
            events.emit("preflight", "started")
        try:
            report = preflight_project(cfg, final=a.final, verify_sources=a.verify_sources,
                                       runtime=runtime)
        except BaseException as exc:
            if events:
                events.emit("preflight", "failed", {"errorType": type(exc).__name__})
            raise
        if events:
            events.emit("preflight", "completed", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if a.preflight:
            return
    pipe = Pipeline(cfg, runtime=runtime, events=events)
    pipe.run(a.from_stage)
    if a.audit:
        # 선택 통합 — auditor 자체는 mp4 단독 입력의 독립 도구 (기본 off)
        from brushvid.audit import run_audit
        audit_out = pipe.data_dir / "audit"
        license_path, report_path = resolved_audit_artifacts(pipe)
        voice_manifest = pipe.data_dir / "tts" / "voice-manifest.json" \
            if cfg.mode == "tts" else None
        if events:
            events.emit("audit", "started")
        try:
            result = run_audit(pipe.output,
                               props=pipe.props_json,
                               out_dir=audit_out,
                               # 명시 cfg가 아니라 실제 mix 결과를 사용한다. 그래야 자동 선택된
                               # 외부 BGM도 라이선스·mix report 감사를 우회하지 않는다.
                               license_manifest=license_path,
                               mix_report=report_path,
                               voice_manifest=voice_manifest,
                               engine_root=runtime.engine_root,
                               workspace_root=runtime.workspace_root,
                               sanitize_paths=runtime.isolated)
        except BaseException as exc:
            if events:
                events.emit("audit", "failed", {"errorType": type(exc).__name__})
            raise
        log.info("audit: %s (FAIL %d / WARN %d) → %s/audit-report.md",
                 result["verdict"], result["summary"]["FAIL"], result["summary"]["WARN"], audit_out)
        if result["verdict"] == "FAIL":
            if events:
                events.emit("audit", "failed", {"verdict": "FAIL"})
            raise SystemExit(1)
        if events:
            events.emit("audit", "completed", {"verdict": result["verdict"]})


if __name__ == "__main__":
    main()

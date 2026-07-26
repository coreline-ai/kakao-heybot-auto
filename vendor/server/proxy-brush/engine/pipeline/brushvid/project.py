"""project.py — project.yaml 로드/검증 + 빌드 모드 판정.

모드 매트릭스:
  - srt + audio           → narration (실더빙 우선 — tts 설정은 무시+경고)
  - srt + tts / script+tts → tts      (Supertonic 합성 더빙, TTS duration이 타이밍 시계)
  - input.audio 만 제공   → whisper   (stt 스테이지로 SRT 생성)
  - 둘 다 없음            → ambient   (300f×N씬 + 합성 BGM + 시적 한줄 cue)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .voice_presets import SPEED_MAX, SPEED_MIN, VoicePresetError, validate_voice_id
from .tts_contract import (
    ENGINE_FIELDS,
    ENGINE_IDS,
    NEW_ENGINE_IDS,
    normalize_language,
    validate_pause_ms,
    validate_reference,
    validate_speed,
)

log = logging.getLogger(__name__)

FORMATS = ("youtube", "shorts")
RENDER_RESOLUTIONS = ("fhd", "uhd")
BG_STRATEGIES = ("imagegen", "preset", "user-images")
BG_FITS = ("contain", "cover")
IMAGEGEN_FALLBACK_POLICIES = ("fail", "preset")
WIDGET_MODES = ("auto", "none", "authored")
TTS_ENGINES = ENGINE_IDS
TTS_TIMINGS = ("tts", "srt")
DRAWING_PROFILES = (
    "brush", "pen", "pen-brush", "dark-random-brush", "cosmic-random-brush",
    "progressive-frame-sequence", "storybook-full-bleed", "full-color-motion",
)
# public name is content-agnostic; keep the historical runtime key for golden/build compatibility.
DRAWING_PROFILE_ALIASES = {"dark-random-brush": "cosmic-random-brush"}
DRAWING_SYNCS = ("auto", "off")
BGM_MODES = ("off", "synth", "asset", "playlist", "piano-auto")
BGM_LICENSE_POLICIES = ("strict", "warn")
MOTION_MOVEMENTS = ("push-in", "push-out", "pan-left", "pan-right", "rise", "fall", "arc-left", "arc-right")
MOTION_EFFECTS = ("none", "rays", "mist", "birds", "stars", "storm", "sparkles", "lanterns",
                  "fireflies", "ripples", "aurora", "trail")
MOTION_REVEALS = ("none", "brush")

# 쇼츠 길이 규정 (2026): 최대 180초 = 앰비언트 18씬(씬당 300f/30fps=10초), 60초 미만 권장
SHORTS_AMBIENT_SCENE_SEC = 10
SHORTS_AMBIENT_MAX_SCENES = 18
SHORTS_RECOMMENDED_SEC = 60


@dataclass(frozen=True)
class BgmConfig:
    """로컬 BGM과 믹싱 설정. None이면 구 프로젝트 오디오 동작을 그대로 사용한다."""

    mode: str
    asset_id: str | None = None
    asset_ids: tuple[str, ...] = ()
    gain_db: float | None = None
    source_start_sec: float = 0.0
    fade_in_sec: float = 1.8
    fade_out_sec: float = 2.0
    crossfade_sec: float = 3.0
    ducking_enabled: bool | None = None
    ducking_amount_db: float = 8.0
    ducking_attack_ms: int = 120
    ducking_release_ms: int = 600
    license_policy: str = "strict"
    prompt: str | None = None
    negative_prompt: str | None = None
    cfg: float = 2.0
    steps: int = 8


@dataclass(frozen=True)
class MotionSceneConfig:
    """Full Color Motion의 씬별 2D 움직임/효과/선택형 붓 리빌 계약."""

    movement: str = "push-in"
    effect: str = "none"
    intensity: float = 1.0
    reveal: str = "none"
    reveal_frames: int = 96


@dataclass(frozen=True)
class MotionConfig:
    """default는 모든 derived scene에 적용하고 scenes는 같은 순서의 명시 override다."""

    default: MotionSceneConfig = MotionSceneConfig()
    scenes: tuple[MotionSceneConfig, ...] = ()

    def scene_for(self, index: int) -> MotionSceneConfig:
        return self.scenes[index] if self.scenes else self.default


@dataclass
class ProjectConfig:
    """검증 완료된 project.yaml 설정. 경로는 yaml 위치 기준으로 해석된 절대 경로."""

    project_id: str
    fmt: str = "youtube"
    render_resolution: str = "fhd"
    title: str | None = None
    srt: Path | None = None
    audio: Path | None = None
    script: Path | None = None
    tts: dict | None = None  # {engine, voice, speed, pauseMs, timing}
    bg_strategy: str = "preset"
    bg_imagegen_fallback: str = "preset"
    bg_subject: str | None = None
    bg_images: list[Path] = field(default_factory=list)
    bg_fit: str = "contain"
    widgets: str = "none"
    authored_widgets: list[dict] = field(default_factory=list)
    drawing_profile: str = "brush"  # public dark-random-brush normalizes to cosmic-random-brush
    drawing_sync: str = "auto"      # 내레이션 동기 드로잉 auto|off (pen+cue 있을 때만 동작)
    drawing_preserve_source: bool = False  # pen 완료 시 원본 전체 색면·그림자 복원
    drawing_full_bleed: bool = False  # pen-brush 풀블리드 원본을 여백 없이 채색
    drawing_viewport_scale: float = 1.0  # pen-brush 화면 채움 확대(모든 레이어 함께 크롭)
    drawing_outline_ratio: float = 0.38
    drawing_handoff_frames: int = 8
    drawing_paint_end_ratio: float = 0.88
    motion: MotionConfig | None = None
    bgm: BgmConfig | None = None
    ambient_scenes: int = 3
    ambient_cues: list[str] = field(default_factory=list)
    subtitle_style: dict = field(default_factory=dict)  # 사용자 명시 subtitleStyle (기본 프리셋에 병합)
    overlays: str = "default"  # default | none — title/cue 자막/위젯 외부 오버레이 정책
    seed: int = 1
    base_dir: Path = Path(".")

    @property
    def mode(self) -> str:
        """narration | tts | whisper | ambient."""
        if self.audio is not None:
            return "narration" if self.srt is not None else "whisper"  # 실더빙 우선
        if self.tts is not None and (self.srt is not None or self.script is not None):
            return "tts"
        if self.srt is not None:
            return "narration"
        if self.audio is not None:
            return "whisper"
        return "ambient"

    def tts_text(self) -> str:
        """TTS 입력 텍스트 — script 우선, 없으면 SRT 의 텍스트만 추출 (타이밍은 버림)."""
        if self.script is not None:
            return self.script.read_text(encoding="utf-8")
        if self.srt is not None:
            from .cues import parse_srt
            return "\n".join(e.text for e in parse_srt(self.srt.read_text(encoding="utf-8")))
        raise ValueError("TTS 입력 텍스트 없음 (script 또는 srt 필요)")


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(f"project.yaml 검증 실패: {msg}")


def _load_motion_scene(raw: object, *, fallback: MotionSceneConfig, label: str) -> MotionSceneConfig:
    """motion.default / motion.scenes[]의 작은 strict 계약을 읽는다.

    movement는 실제 3D camera가 아니라 정지 이미지에 적용되는 2D 프리셋이다.
    raw camera/cameraMotion 키를 받지 않아 Director의 Camera Prompt Pack과 runtime을
    혼동하지 않는다.
    """
    _require(isinstance(raw, dict), f"{label} 는 매핑이어야 함")
    allowed = {"movement", "effect", "intensity", "reveal", "revealFrames"}
    unknown = sorted(set(raw) - allowed)
    _require(not unknown, f"{label} 지원하지 않는 옵션: {', '.join(unknown)}")
    movement = raw.get("movement", fallback.movement)
    effect = raw.get("effect", fallback.effect)
    reveal = raw.get("reveal", fallback.reveal)
    _require(movement in MOTION_MOVEMENTS,
             f"{label}.movement 는 {MOTION_MOVEMENTS} 중 하나여야 함 (입력: {movement!r})")
    _require(effect in MOTION_EFFECTS,
             f"{label}.effect 는 {MOTION_EFFECTS} 중 하나여야 함 (입력: {effect!r})")
    _require(reveal in MOTION_REVEALS,
             f"{label}.reveal 는 {MOTION_REVEALS} 중 하나여야 함 (입력: {reveal!r})")
    intensity = float(raw.get("intensity", fallback.intensity))
    reveal_frames = int(raw.get("revealFrames", fallback.reveal_frames))
    _require(0.25 <= intensity <= 2.0, f"{label}.intensity 는 0.25~2.0")
    _require(30 <= reveal_frames <= 180, f"{label}.revealFrames 는 30~180")
    return MotionSceneConfig(
        movement=movement, effect=effect, intensity=intensity,
        reveal=reveal, reveal_frames=reveal_frames,
    )


def _load_motion(raw: object) -> MotionConfig:
    _require(isinstance(raw, dict), "motion 은 매핑이어야 함")
    allowed = {"default", "scenes"}
    unknown = sorted(set(raw) - allowed)
    _require(not unknown, f"motion 지원하지 않는 옵션: {', '.join(unknown)}")
    default_raw = raw.get("default") or {}
    default = _load_motion_scene(default_raw, fallback=MotionSceneConfig(), label="motion.default")
    scenes_raw = raw.get("scenes") or []
    _require(isinstance(scenes_raw, list), "motion.scenes 는 목록이어야 함")
    scenes = tuple(
        _load_motion_scene(item, fallback=default, label=f"motion.scenes[{index}]")
        for index, item in enumerate(scenes_raw)
    )
    return MotionConfig(default=default, scenes=scenes)


def load_project(yaml_path: str | Path) -> ProjectConfig:
    """project.yaml 로드 + 검증. 위반 시 ValueError (파이프라인 미진입)."""
    yaml_path = Path(yaml_path).resolve()
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    _require(isinstance(raw, dict), "최상위가 매핑이 아님")
    base = yaml_path.parent

    project_id = raw.get("projectId")
    _require(isinstance(project_id, str) and project_id.strip() != "", "projectId 필수")
    fmt = raw.get("format", "youtube")
    _require(fmt in FORMATS, f"format 은 {FORMATS} 중 하나여야 함 (입력: {fmt!r})")

    render_raw = raw.get("render") or {}
    _require(isinstance(render_raw, dict), "render 는 매핑이어야 함")
    unknown_render = sorted(set(render_raw) - {"resolution"})
    _require(not unknown_render, f"render 지원하지 않는 옵션: {', '.join(unknown_render)}")
    render_resolution = render_raw.get("resolution", "fhd")
    _require(render_resolution in RENDER_RESOLUTIONS,
             f"render.resolution 은 {RENDER_RESOLUTIONS} 중 하나여야 함 (입력: {render_resolution!r})")
    _require(not (fmt == "shorts" and render_resolution == "uhd"),
             "render.resolution=uhd 는 현재 format: youtube 에서만 지원")

    inp = raw.get("input") or {}
    _require(isinstance(inp, dict), "input 은 매핑이어야 함")
    srt = inp.get("srt")
    audio = inp.get("audio")
    script = inp.get("script")
    srt_p = (base / srt).resolve() if srt else None
    audio_p = (base / audio).resolve() if audio else None
    script_p = (base / script).resolve() if script else None
    if srt_p is not None:
        _require(srt_p.is_file(), f"input.srt 파일 없음: {srt_p}")
    if audio_p is not None:
        _require(audio_p.is_file(), f"input.audio 파일 없음: {audio_p}")
    if script_p is not None:
        _require(script_p.is_file(), f"input.script 파일 없음: {script_p}")

    # input.tts — 엔진별 허용 필드와 입력 source를 먼저 고정한다.
    tts_raw = inp.get("tts")
    tts_cfg: dict | None = None
    if tts_raw is not None:
        _require(isinstance(tts_raw, dict), "input.tts 는 매핑이어야 함")
        engine = tts_raw.get("engine", "supertonic")
        _require(engine in TTS_ENGINES, f"input.tts.engine 은 {TTS_ENGINES} 중 하나여야 함 (입력: {engine!r})")
        unknown_tts = sorted(set(tts_raw) - ENGINE_FIELDS[engine])
        _require(not unknown_tts, f"input.tts 지원하지 않는 옵션: {', '.join(unknown_tts)}")
        timing = tts_raw.get("timing", "tts")
        _require(timing in TTS_TIMINGS, f"input.tts.timing 은 {TTS_TIMINGS} 중 하나여야 함 (입력: {timing!r})")
        if engine in NEW_ENGINE_IDS:
            _require(timing == "tts", f"{engine}은 input.tts.timing=tts만 지원함")
        pause_ms = tts_raw.get("pauseMs", 300)
        try:
            pause_ms = validate_pause_ms(pause_ms)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        voice_default = "kr-default" if engine == "melo-ko" else (None if engine == "qwen3-base" else "F1")
        voice_raw = tts_raw.get("voice", voice_default)
        _require(isinstance(voice_raw, str) and bool(voice_raw.strip()),
                 "input.tts.voice 는 비어 있지 않은 문자열이어야 함")
        if engine == "supertonic":
            try:
                voice = validate_voice_id(voice_raw)
            except VoicePresetError as exc:
                raise ValueError(str(exc)) from exc
        elif engine == "melo-ko":
            _require(voice_raw == "kr-default", "melo-ko voice는 kr-default만 지원함")
            voice = voice_raw
        else:
            voice = voice_raw
        language_raw = tts_raw.get("language", "ko")
        try:
            language = normalize_language(engine, language_raw)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        speed_raw = tts_raw.get("speed", 1.05)
        try:
            speed = validate_speed(speed_raw, minimum=SPEED_MIN, maximum=SPEED_MAX)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        tts_cfg = {"engine": engine, "voice": voice, "speed": speed,
                   "pauseMs": pause_ms, "timing": timing}
        if "language" in tts_raw:
            tts_cfg["language"] = language
        if engine == "qwen3-base":
            try:
                tts_cfg["reference"] = validate_reference(tts_raw.get("reference"), base_dir=base)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        if audio_p is not None:
            log.warning("input.audio(실더빙)와 tts 가 함께 설정됨 — 실더빙 우선, TTS 무시")
    if audio_p is None and script_p is not None and srt_p is not None and tts_cfg is not None:
        raise ValueError("input.script와 input.srt를 함께 사용할 수 없음: TTS 텍스트 source가 모호함")
    if script_p is not None:
        _require(tts_cfg is not None, "input.script 은 input.tts 와 함께 사용해야 함 (타이밍/더빙 소스 없음)")

    bg = raw.get("background") or {}
    _require(isinstance(bg, dict), "background 는 매핑이어야 함")
    strategy = bg.get("strategy", "preset")
    _require(strategy in BG_STRATEGIES, f"background.strategy 는 {BG_STRATEGIES} 중 하나여야 함 (입력: {strategy!r})")
    bg_fit = bg.get("fit", "contain")
    _require(bg_fit in BG_FITS, f"background.fit 은 {BG_FITS} 중 하나여야 함 (입력: {bg_fit!r})")
    images = [(base / p).resolve() for p in (bg.get("images") or [])]
    if strategy == "user-images":
        _require(len(images) >= 1, "user-images 전략에는 background.images 최소 1장 필요")
        for p in images:
            _require(p.is_file(), f"background.images 파일 없음: {p}")

    # draw_proxy의 요청 fallbackPolicy는 엔진 실행 전에 정규화되어 전달된다.
    # 직접 엔진을 호출하는 기존 프로젝트는 명시하지 않아도 이전처럼 preset을 쓴다.
    fallback_policy = raw.get("fallbackPolicy") or {}
    _require(isinstance(fallback_policy, dict), "fallbackPolicy 는 매핑이어야 함")
    imagegen_fallback = fallback_policy.get("imagegen", "preset")
    _require(imagegen_fallback in IMAGEGEN_FALLBACK_POLICIES,
             f"fallbackPolicy.imagegen 은 {IMAGEGEN_FALLBACK_POLICIES} 중 하나여야 함 "
             f"(입력: {imagegen_fallback!r})")

    widgets_raw = raw.get("widgets", "none")
    authored: list[dict] = []
    if isinstance(widgets_raw, list):  # 인라인 authored 위젯 목록
        widgets_mode = "authored"
        authored = widgets_raw
    else:
        widgets_mode = widgets_raw
        _require(widgets_mode in WIDGET_MODES, f"widgets 는 {WIDGET_MODES} 또는 위젯 목록이어야 함 (입력: {widgets_raw!r})")
    if widgets_mode == "auto":
        # 위젯 auto 배치는 위젯 워크스트림 통합 후 지원 — 지금은 none 으로 처리
        log.warning("widgets: auto 는 보류 상태 — none 으로 처리합니다")
        widgets_mode = "none"

    # drawing — {profile: brush|pen}. 기본 brush, 오타는 즉시 거부.
    drawing = raw.get("drawing") or {}
    _require(isinstance(drawing, dict), "drawing 은 매핑이어야 함")
    profile_input = drawing.get("profile", "brush")
    _require(profile_input in DRAWING_PROFILES,
             f"drawing.profile 은 {DRAWING_PROFILES} 중 하나여야 함 (입력: {profile_input!r})")
    profile = DRAWING_PROFILE_ALIASES.get(profile_input, profile_input)
    sync = drawing.get("sync", "auto")
    if isinstance(sync, bool):  # YAML bare off/on 은 불리언으로 파싱됨
        sync = "auto" if sync else "off"
    _require(sync in DRAWING_SYNCS,
             f"drawing.sync 는 {DRAWING_SYNCS} 중 하나여야 함 (입력: {sync!r})")
    preserve_source = drawing.get("preserveSource", False)
    _require(isinstance(preserve_source, bool), "drawing.preserveSource 는 true/false")
    _require(not preserve_source or profile == "pen",
             "drawing.preserveSource 는 drawing.profile: pen 에서만 지원")
    full_bleed = drawing.get("fullBleed", False)
    _require(isinstance(full_bleed, bool), "drawing.fullBleed 는 true/false")
    _require(not full_bleed or profile == "pen-brush",
             "drawing.fullBleed 는 drawing.profile: pen-brush 에서만 지원")
    # contain is intentionally inset by a paper border.  It is incompatible
    # with an animation promised as full-bleed.
    _require(not full_bleed or bg_fit == "cover",
             "drawing.fullBleed 는 background.fit: cover 필수 (contain은 외곽 종이 여백을 만듦)")
    allowed_drawing = {"profile", "sync", "seed", "preserveSource", "fullBleed", "viewportScale", "outlineRatio", "handoffFrames", "paintEndRatio"}
    unknown_drawing = sorted(set(drawing) - allowed_drawing)
    _require(not unknown_drawing, f"drawing 지원하지 않는 옵션: {', '.join(unknown_drawing)}")
    viewport_scale = float(drawing.get("viewportScale", 1.0))
    _require(1.0 <= viewport_scale <= 1.35, "drawing.viewportScale 는 1.00~1.35")
    _require(viewport_scale == 1.0 or profile == "pen-brush",
             "drawing.viewportScale 는 drawing.profile: pen-brush 에서만 지원")
    outline_ratio = float(drawing.get("outlineRatio", 0.38))
    handoff_frames = int(drawing.get("handoffFrames", 8))
    paint_end_ratio = float(drawing.get("paintEndRatio", 0.88))
    _require(0.20 <= outline_ratio <= 0.55, "drawing.outlineRatio 는 0.20~0.55")
    _require(0 <= handoff_frames <= 30, "drawing.handoffFrames 는 0~30")
    _require(outline_ratio + 0.05 < paint_end_ratio <= 0.95,
             "drawing.paintEndRatio 는 outlineRatio+0.05 초과, 0.95 이하")

    # Full Color Motion은 원본 bitmap을 cover-fit으로 보존하고 routes는 선택형
    # brush reveal에서만 사용한다. motion block은 다른 drawing profile에서는
    # 침묵 무시하지 않고 입력 오류로 처리한다.
    motion_cfg: MotionConfig | None = None
    if "motion" in raw:
        _require(profile == "full-color-motion", "motion 은 drawing.profile: full-color-motion 에서만 지원")
        motion_cfg = _load_motion(raw.get("motion"))
    if profile == "full-color-motion":
        _require(motion_cfg is not None, "full-color-motion에는 motion 블록이 필요")
        _require(strategy == "user-images", "full-color-motion은 background.strategy: user-images 필수")
        _require(bg_fit == "cover", "full-color-motion은 background.fit: cover 필수")
        _require(not preserve_source and not full_bleed,
                 "full-color-motion은 drawing.preserveSource/fullBleed를 지원하지 않음")

    # bgm — input.audio(내레이션)와 완전히 분리된 로컬 음악 트랙.
    # 블록 자체가 없으면 기존 프로젝트의 오디오 동작을 보존한다.
    bgm_cfg: BgmConfig | None = None
    if "bgm" in raw:
        bgm_raw = raw.get("bgm")
        _require(isinstance(bgm_raw, dict), "bgm 은 매핑이어야 함")
        allowed_bgm = {"mode", "assetId", "gainDb", "sourceStartSec", "fadeInSec", "fadeOutSec",
                       "playlist", "ducking", "licensePolicy", "prompt", "negativePrompt", "cfg", "steps"}
        unknown_bgm = sorted(set(bgm_raw) - allowed_bgm)
        _require(not unknown_bgm, f"bgm 지원하지 않는 옵션: {', '.join(unknown_bgm)}")

        bgm_mode = bgm_raw.get("mode", "asset")
        _require(bgm_mode in BGM_MODES,
                 f"bgm.mode 은 {BGM_MODES} 중 하나여야 함 (입력: {bgm_mode!r})")
        asset_id = bgm_raw.get("assetId")
        if asset_id is not None:
            _require(isinstance(asset_id, str) and asset_id.strip(), "bgm.assetId 는 빈 문자열일 수 없음")
            asset_id = asset_id.strip()

        playlist_raw = bgm_raw.get("playlist") or {}
        _require(isinstance(playlist_raw, dict), "bgm.playlist 는 매핑이어야 함")
        unknown_playlist = sorted(set(playlist_raw) - {"assetIds", "crossfadeSec"})
        _require(not unknown_playlist,
                 f"bgm.playlist 지원하지 않는 옵션: {', '.join(unknown_playlist)}")
        ids_raw = playlist_raw.get("assetIds") or []
        _require(isinstance(ids_raw, list) and all(isinstance(v, str) and v.strip() for v in ids_raw),
                 "bgm.playlist.assetIds 는 비어 있지 않은 문자열 목록이어야 함")
        asset_ids = tuple(v.strip() for v in ids_raw)

        if bgm_mode == "asset":
            _require(asset_id is not None, "bgm.mode=asset 은 bgm.assetId 필수")
            _require(not asset_ids, "bgm.mode=asset 에 playlist.assetIds 를 함께 지정할 수 없음")
        elif bgm_mode == "playlist":
            _require(asset_id is None, "bgm.mode=playlist 에 assetId 를 함께 지정할 수 없음")
            _require(2 <= len(asset_ids) <= 3, "bgm.mode=playlist 는 assetIds 2~3개 필요")
        else:
            _require(asset_id is None and not asset_ids,
                     f"bgm.mode={bgm_mode} 에 assetId/playlist 를 지정할 수 없음")

        gain_db = bgm_raw.get("gainDb")
        if gain_db is not None:
            gain_db = float(gain_db)
            _require(-24.0 <= gain_db <= 12.0, "bgm.gainDb 는 -24~12")
        source_start = float(bgm_raw.get("sourceStartSec", 0.0))
        fade_in = float(bgm_raw.get("fadeInSec", 1.8))
        fade_out = float(bgm_raw.get("fadeOutSec", 2.0))
        crossfade = float(playlist_raw.get("crossfadeSec", 3.0))
        _require(0.0 <= source_start <= 60.0, "bgm.sourceStartSec 는 0~60")
        if bgm_mode in ("off", "synth"):
            _require(source_start == 0.0,
                     f"bgm.mode={bgm_mode} 에 sourceStartSec 를 지정할 수 없음")
        _require(0.0 <= fade_in <= 10.0, "bgm.fadeInSec 는 0~10")
        _require(0.0 <= fade_out <= 10.0, "bgm.fadeOutSec 는 0~10")
        _require(0.5 <= crossfade <= 10.0, "bgm.playlist.crossfadeSec 는 0.5~10")

        duck_raw = bgm_raw.get("ducking") or {}
        _require(isinstance(duck_raw, dict), "bgm.ducking 은 매핑이어야 함")
        unknown_duck = sorted(set(duck_raw) - {"enabled", "amountDb", "attackMs", "releaseMs"})
        _require(not unknown_duck,
                 f"bgm.ducking 지원하지 않는 옵션: {', '.join(unknown_duck)}")
        duck_enabled = duck_raw.get("enabled")
        _require(duck_enabled is None or isinstance(duck_enabled, bool),
                 "bgm.ducking.enabled 는 true/false")
        duck_amount = float(duck_raw.get("amountDb", 8.0))
        duck_attack = int(duck_raw.get("attackMs", 120))
        duck_release = int(duck_raw.get("releaseMs", 600))
        _require(0.0 <= duck_amount <= 24.0, "bgm.ducking.amountDb 는 0~24")
        _require(1 <= duck_attack <= 2000, "bgm.ducking.attackMs 는 1~2000")
        _require(10 <= duck_release <= 5000, "bgm.ducking.releaseMs 는 10~5000")
        license_policy = bgm_raw.get("licensePolicy", "strict")
        _require(license_policy in BGM_LICENSE_POLICIES,
                 f"bgm.licensePolicy 는 {BGM_LICENSE_POLICIES} 중 하나여야 함")

        prompt = bgm_raw.get("prompt")
        negative_prompt = bgm_raw.get("negativePrompt")
        _require(prompt is None or (isinstance(prompt, str) and 1 <= len(prompt.strip()) <= 1000),
                 "bgm.prompt 는 1~1000자의 문자열이어야 함")
        _require(negative_prompt is None or (isinstance(negative_prompt, str) and 1 <= len(negative_prompt.strip()) <= 1000),
                 "bgm.negativePrompt 는 1~1000자의 문자열이어야 함")
        cfg_scale = float(bgm_raw.get("cfg", 2.0))
        steps = int(bgm_raw.get("steps", 8))
        _require(bgm_mode == "piano-auto" or (prompt is None and negative_prompt is None and "cfg" not in bgm_raw and "steps" not in bgm_raw),
                 "prompt/negativePrompt/cfg/steps 는 bgm.mode=piano-auto 에서만 사용 가능")
        _require(0.0 <= cfg_scale <= 10.0, "bgm.cfg 는 0~10")
        _require(1 <= steps <= 16, "bgm.steps 는 1~16")

        bgm_cfg = BgmConfig(
            mode=bgm_mode, asset_id=asset_id, asset_ids=asset_ids, gain_db=gain_db,
            source_start_sec=source_start, fade_in_sec=fade_in,
            fade_out_sec=fade_out, crossfade_sec=crossfade,
            ducking_enabled=duck_enabled, ducking_amount_db=duck_amount,
            ducking_attack_ms=duck_attack, ducking_release_ms=duck_release,
            license_policy=license_policy, prompt=prompt.strip() if isinstance(prompt, str) else None,
            negative_prompt=negative_prompt.strip() if isinstance(negative_prompt, str) else None,
            cfg=cfg_scale, steps=steps,
        )

    amb = raw.get("ambient") or {}
    _require(isinstance(amb, dict), "ambient 는 매핑이어야 함")
    ambient_scenes = int(amb.get("scenes", 3))
    _require(ambient_scenes >= 1, "ambient.scenes 는 1 이상")
    ambient_cues = amb.get("cues") or []
    _require(isinstance(ambient_cues, list)
             and all(isinstance(cue, str) and cue.strip() for cue in ambient_cues),
             "ambient.cues 는 비어 있지 않은 문자열 목록이어야 함")

    subtitle_style = raw.get("subtitleStyle") or {}
    _require(isinstance(subtitle_style, dict), "subtitleStyle 은 매핑이어야 함")

    overlays = raw.get("overlays", "default")
    _require(overlays in {"default", "none"}, "overlays 는 default 또는 none 이어야 함")

    cfg = ProjectConfig(
        project_id=project_id.strip(),
        fmt=fmt,
        render_resolution=render_resolution,
        title=raw.get("title"),
        srt=srt_p,
        audio=audio_p,
        script=script_p,
        tts=tts_cfg,
        bg_strategy=strategy,
        bg_imagegen_fallback=imagegen_fallback,
        bg_subject=bg.get("subject") or bg.get("style"),
        bg_images=images,
        bg_fit=bg_fit,
        widgets=widgets_mode,
        authored_widgets=authored,
        drawing_profile=profile,
        drawing_sync=sync,
        drawing_preserve_source=preserve_source,
        drawing_full_bleed=full_bleed,
        drawing_viewport_scale=viewport_scale,
        drawing_outline_ratio=outline_ratio,
        drawing_handoff_frames=handoff_frames,
        drawing_paint_end_ratio=paint_end_ratio,
        motion=motion_cfg,
        bgm=bgm_cfg,
        ambient_scenes=ambient_scenes,
        ambient_cues=list(ambient_cues),
        subtitle_style=subtitle_style,
        overlays=overlays,
        seed=int(drawing.get("seed", raw.get("seed", 1))),
        base_dir=base,
    )

    # 쇼츠 길이 규정: 앰비언트 18씬(180초) 한도 초과는 에러, 60초 초과는 권장 경고만
    if cfg.fmt == "shorts" and cfg.mode == "ambient":
        _require(cfg.ambient_scenes <= SHORTS_AMBIENT_MAX_SCENES,
                 f"쇼츠 앰비언트 scenes 는 최대 {SHORTS_AMBIENT_MAX_SCENES}"
                 f" (180초 한도, 입력: {cfg.ambient_scenes})")
        total_sec = cfg.ambient_scenes * SHORTS_AMBIENT_SCENE_SEC
        if total_sec > SHORTS_RECOMMENDED_SEC:
            log.warning("쇼츠 총 길이 %d초 — 초기 노출은 %d초 미만 권장",
                        total_sec, SHORTS_RECOMMENDED_SEC)
    if cfg.drawing_profile == "cosmic-random-brush":
        _require(cfg.fmt == "youtube", "cosmic-random-brush는 format: youtube만 지원")
        _require(cfg.mode == "ambient", "cosmic-random-brush는 ambient 모드만 지원")
        _require(cfg.ambient_scenes in (1, 6, 60),
                 "cosmic-random-brush는 ambient.scenes: 1(골든), 6(v0.2), 60(v0.3)만 지원")
        if cfg.bg_strategy == "user-images":
            _require(len(cfg.bg_images) == cfg.ambient_scenes,
                     "cosmic-random-brush user-images는 background.images 수가 ambient.scenes와 같아야 함")
        if cfg.ambient_scenes in (6, 60):
            _require(cfg.bg_strategy == "user-images",
                     "cosmic-random-brush 6/60씬은 background.strategy: user-images 필수")
        if cfg.ambient_scenes == 60:
            _require(len(cfg.ambient_cues) == 60,
                     "cosmic-random-brush 60씬은 ambient.cues 60개가 필요")
    if cfg.drawing_profile == "full-color-motion":
        assert cfg.motion is not None  # 위 validator가 보장
        if cfg.mode == "ambient":
            _require(len(cfg.bg_images) == cfg.ambient_scenes,
                     "full-color-motion ambient는 background.images 수가 ambient.scenes와 같아야 함")
            _require(not cfg.motion.scenes or len(cfg.motion.scenes) == cfg.ambient_scenes,
                     "full-color-motion motion.scenes 수가 ambient.scenes와 같아야 함")
    return cfg

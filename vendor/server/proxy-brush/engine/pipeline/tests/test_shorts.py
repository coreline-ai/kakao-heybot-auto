"""shorts(세로) 워크스트림 테스트 — 세로 정합(Phase 1) + 연출 프리셋(Phase 2) + 가로 무변경."""
import hashlib
import importlib.util
import json
import logging
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from brushvid.background import generate, _pick_prompt, _PROMPT_TEMPLATE, _PROMPT_TEMPLATE_PORTRAIT
from brushvid.layout import validate_layout
from brushvid.project import load_project
from brushvid.routes import RouteParams, generate_routes

BUILD_PY = Path(__file__).resolve().parents[2] / "bin" / "build.py"
spec = importlib.util.spec_from_file_location("buildmod_shorts", BUILD_PY)
buildmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(buildmod)

PORTRAIT = (1080, 1920)

# 변경 전(2026-07-11, W/H 하드코딩 시절) preset 배경 sha256 — 가로 경로 바이트 무변경 증명
PRESET_BASELINE_SHA256 = {
    7: "fb4c47e62fab74d643c76ec12ffb36c525994c0f180eaec6e2172903583c29cd",
    42: "f5b69b74ef983e0dfeb3cf24d82bb302ffb1c691629d7d04ede93dea464c8c1e",
}


# ── Phase 1: 세로 파이프라인 정합 ──

def test_preset_portrait_resolution(tmp_path):
    """preset 배경이 세로 캔버스(1080×1920)로 생성된다."""
    out = tmp_path / "bg.png"
    res = generate("preset", out, seed=7, size=PORTRAIT)
    assert res["strategy"] == "preset"
    assert Image.open(out).size == PORTRAIT


def test_user_images_portrait_resolution(tmp_path):
    """user-images 전략도 세로 캔버스를 지원한다."""
    src = tmp_path / "src.png"
    Image.new("RGB", (640, 640), (30, 60, 120)).save(src)
    out = tmp_path / "bg.png"
    generate("user-images", out, images=[src], size=PORTRAIT)
    assert Image.open(out).size == PORTRAIT


def test_prompt_template_by_orientation():
    """imagegen 프롬프트: 세로 캔버스면 세로 템플릿, 가로면 기존 템플릿(무변경)."""
    assert _pick_prompt((1920, 1080)) is _PROMPT_TEMPLATE
    assert _pick_prompt(PORTRAIT) is _PROMPT_TEMPLATE_PORTRAIT
    assert "9:16" in _PROMPT_TEMPLATE_PORTRAIT and "portrait" in _PROMPT_TEMPLATE_PORTRAIT
    assert "16:9" in _PROMPT_TEMPLATE  # 기존 가로 템플릿 그대로


def test_routes_portrait_meta_and_bounds(tmp_path):
    """세로 캔버스 routes: meta 1080×1920 + 모든 점이 캔버스 안."""
    img = Image.new("RGB", PORTRAIT, (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([340, 760, 740, 1160], outline=(0, 0, 0), width=24)
    p = tmp_path / "circle-portrait.png"
    img.save(p)
    data = generate_routes(p, RouteParams(width=1080, height=1920, duration=300,
                                          draw_start=8, draw_end=220, seed=1))
    assert (data["meta"]["width"], data["meta"]["height"]) == PORTRAIT
    assert data["meta"]["coverage"] >= 0.95
    for s in data["strokes"]:
        for x, y in s["points"]:
            assert 0 <= x <= 1080 and 0 <= y <= 1920


def test_horizontal_preset_byte_identical(tmp_path):
    """가로(기본) preset 배경은 인자화 이후에도 변경 전 스냅샷과 바이트 동일."""
    for seed, expected in PRESET_BASELINE_SHA256.items():
        out = tmp_path / f"bg-{seed}.png"
        generate("preset", out, seed=seed)  # size 미지정 = 기존 1920×1080 경로
        assert hashlib.sha256(out.read_bytes()).hexdigest() == expected


def test_portrait_preset_scene_colors_distinct(tmp_path):
    """세로 preset: 씬 시드 회전 팔레트 → title_color 추출색이 씬마다 다르다."""
    from brushvid.background import clean
    from brushvid.cues import title_color
    colors = []
    for seed in (11, 48, 85):  # 씬별 시드 규약 (seed + i*37)
        bg = tmp_path / f"bg-{seed}.png"
        generate("preset", bg, seed=seed, size=PORTRAIT)
        content = clean(bg, tmp_path / f"content-{seed}.png")
        colors.append(title_color(content, min_sat=14))
    assert len(set(colors)) == 3, colors
    assert "#5a544c" not in colors  # 채도-없음 폴백이 아님


# ── Phase 2: 세로 세이프존 (layout) ──

def test_portrait_top_safezone_hard_fail():
    """세로: 위젯 y < 120 → 상단 세이프존 hard-fail."""
    widgets = [{"type": "stat", "x": 200, "y": 100, "w": 400, "h": 200}]
    res = validate_layout(widgets, canvas=PORTRAIT)
    assert any("상단 세이프존" in f for f in res.fails)


def test_portrait_subtitle_band_hard_fail():
    """세로: 위젯이 하단 자막 밴드(y+h > H-290) 침범 → hard-fail."""
    widgets = [{"type": "stat", "x": 200, "y": 1500, "w": 400, "h": 200}]  # 1700 > 1630
    res = validate_layout(widgets, canvas=PORTRAIT)
    assert any("하단 자막 밴드" in f for f in res.fails)


def test_portrait_top_title_safezone():
    """세로: topTitle y < 120 → hard-fail, y ≥ 120 은 통과."""
    res = validate_layout([], top_title={"lines": ["타이틀"], "y": 74}, canvas=PORTRAIT)
    assert any("topTitle" in f for f in res.fails)
    ok = validate_layout([], top_title={"lines": ["타이틀"], "y": 140}, canvas=PORTRAIT)
    assert ok.ok, ok.fails


def test_portrait_valid_layout_passes():
    """세로 세이프존 안의 위젯 배치는 통과한다."""
    widgets = [{"type": "stat", "x": 120, "y": 300, "w": 400, "h": 200}]
    res = validate_layout(widgets, canvas=PORTRAIT, has_cues=True)
    assert res.ok, res.fails


def test_landscape_layout_unchanged():
    """가로(기본 canvas): 세로 세이프존 규칙이 적용되지 않는다 (기존 동작 무변경)."""
    widgets = [{"type": "stat", "x": 200, "y": 100, "w": 400, "h": 200}]  # y=100 — 가로에선 유효
    res = validate_layout(widgets)
    assert res.ok, res.fails


# ── Phase 2: 쇼츠 길이 규정 (project) ──

def _write_yaml(tmp_path, body: str):
    p = tmp_path / "project.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_shorts_ambient_scenes_over_18_rejected(tmp_path):
    """shorts + ambient scenes 19 → 검증 에러 (180초 한도)."""
    with pytest.raises(ValueError, match="18"):
        load_project(_write_yaml(tmp_path, """
projectId: demo
format: shorts
ambient:
  scenes: 19
"""))


def test_shorts_ambient_over_60s_warns_only(tmp_path, caplog):
    """shorts + ambient 7씬(70초) → 에러 없이 권장 경고 로그만."""
    with caplog.at_level(logging.WARNING, logger="brushvid.project"):
        cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
format: shorts
ambient:
  scenes: 7
"""))
    assert cfg.ambient_scenes == 7
    assert any("60초 미만 권장" in r.message for r in caplog.records)


def test_shorts_ambient_3_scenes_no_warning(tmp_path, caplog):
    """기본 3씬(30초)은 경고 없이 통과."""
    with caplog.at_level(logging.WARNING, logger="brushvid.project"):
        cfg = load_project(_write_yaml(tmp_path, """
projectId: demo
format: shorts
ambient:
  scenes: 3
"""))
    assert cfg.ambient_scenes == 3
    assert not any("권장" in r.message for r in caplog.records)


# ── Phase 2: 쇼츠 연출 프리셋 (props 주입) ──

@pytest.fixture()
def shorts_pipe(tmp_path, monkeypatch):
    """stage_props 만 실행 가능한 최소 shorts 앰비언트 파이프라인 (3씬 + 색상 상이 배경)."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="shorts-unit", fmt="shorts")
    pipe = buildmod.Pipeline(cfg)
    scenes = [{"durationInFrames": 300,
               "cues": [{"text": f"cue {i}", "from": 40, "to": 260}]} for i in range(3)]
    pipe._write_scenes(scenes)
    colors = [(180, 40, 40), (40, 150, 60), (40, 70, 180)]  # 씬별 도미넌트 색 상이
    for i, col in enumerate(colors):
        p = pipe.public_dir / "bg" / f"scene-{i + 1:02d}-content.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (480, 270), col).save(p)
    return pipe


def test_shorts_props_presets_injected(shorts_pipe):
    """subtitleStyle 세이프존 + 씬별 highlightColor 상이 + 훅/전환/루프 프리셋."""
    shorts_pipe.stage_props()
    props = json.loads(shorts_pipe.props_json.read_text(encoding="utf-8"))
    scenes = props["scenes"]
    assert len(scenes) == 3

    highlights = []
    for sc in scenes:
        st = sc["subtitleStyle"]
        assert (st["bottom"], st["maxWidth"], st["fontSize"]) == (290, 900, 36)
        highlights.append(st["highlightColor"])
        assert sc["outroFadeFrames"] == 18  # 모든 씬 전환 페이드
    assert len(set(highlights)) == 3  # 씬별 강조색 상이 (배경 동조)

    first, last = scenes[0], scenes[-1]
    assert first["prewashOpacity"] == 0.5 and first["prewashFrames"] == 18
    assert first["prewashHoldFrames"] == 6 and first["prewashBlur"] == 12
    assert last["outroWashOpacity"] == 1.0  # 루프: 순백 수렴
    assert "outroWashOpacity" not in scenes[0]  # 마지막 씬에만


def test_shorts_props_user_subtitle_style_respected(shorts_pipe):
    """사용자가 subtitleStyle 을 명시하면 기본 프리셋보다 우선한다."""
    shorts_pipe.cfg.subtitle_style = {"fontSize": 42, "highlightColor": "#112233"}
    shorts_pipe.stage_props()
    props = json.loads(shorts_pipe.props_json.read_text(encoding="utf-8"))
    st = props["scenes"][0]["subtitleStyle"]
    assert st["fontSize"] == 42          # 사용자 값 우선
    assert st["bottom"] == 290           # 미지정 키는 프리셋 유지
    assert st["highlightColor"] == "#112233"  # 명시 시 씬 추출색 대체 안 함


def test_youtube_props_not_injected(tmp_path, monkeypatch):
    """가로 brush는 쇼츠 프리셋 대신 공용 무펄스 완료 프리셋을 사용한다."""
    monkeypatch.setattr(buildmod, "REPO_ROOT", tmp_path)
    cfg = buildmod.ProjectConfig(project_id="yt-unit", fmt="youtube")
    pipe = buildmod.Pipeline(cfg)
    pipe._write_scenes([{"durationInFrames": 300,
                         "cues": [{"text": "cue", "from": 40, "to": 260}]}])
    route_dir = pipe.public_dir / "routes"
    route_dir.mkdir(parents=True)
    (route_dir / "scene-01.routes.json").write_text(json.dumps({
        "meta": {"drawStart": 8, "drawEnd": 219, "penInvisibleAfter": 228},
        "strokes": [{"end": 219}],
    }), encoding="utf-8")
    pipe.stage_props()
    props = json.loads(pipe.props_json.read_text(encoding="utf-8"))
    sc = props["scenes"][0]
    assert "subtitleStyle" not in sc
    assert sc["completionMode"] == "integrated-develop"
    assert sc["outroFadeFrames"] == 24 and sc["outroWashOpacity"] == 1.0
    assert sc["prewashImage"] == "yt-unit/bg/scene-01.png"
    assert sc["prewashOpacity"] == 0.5
    assert sc["prewashFrames"] == 12
    assert sc["prewashFadeOutFrames"] == 12
    assert sc["prewashBlur"] == 12

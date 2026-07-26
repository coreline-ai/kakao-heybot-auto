"""pen 프로파일 테스트 — profile 파싱 / separate_ink / pen routes 파라미터."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from brushvid.background import PEN_PAPER, separate_ink
from brushvid.project import load_project

BUILD_PY = Path(__file__).resolve().parents[2] / "bin" / "build.py"
spec = importlib.util.spec_from_file_location("buildmod_pen", BUILD_PY)
buildmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(buildmod)


def _yaml(tmp_path, body):
    p = tmp_path / "project.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_profile_default_brush(tmp_path):
    cfg = load_project(_yaml(tmp_path, "projectId: demo\n"))
    assert cfg.drawing_profile == "brush"


def test_profile_pen(tmp_path):
    cfg = load_project(_yaml(tmp_path, "projectId: demo\ndrawing:\n  profile: pen\n"))
    assert cfg.drawing_profile == "pen"
    assert cfg.drawing_preserve_source is False


def test_pen_preserve_source_option(tmp_path):
    cfg = load_project(_yaml(
        tmp_path,
        "projectId: demo\ndrawing:\n  profile: pen\n  preserveSource: true\n",
    ))
    assert cfg.drawing_preserve_source is True
    with pytest.raises(ValueError, match="preserveSource"):
        load_project(_yaml(
            tmp_path,
            "projectId: demo\ndrawing:\n  profile: brush\n  preserveSource: true\n",
        ))


def test_profile_typo_rejected(tmp_path):
    with pytest.raises(ValueError, match="drawing.profile"):
        load_project(_yaml(tmp_path, "projectId: demo\ndrawing:\n  profile: penn\n"))


def test_separate_ink_line_only(tmp_path):
    """검은 선 + 밝은 회색 그라데이션 → 알파에는 선만 남는다 (그라데이션은 종이 취급)."""
    img = Image.new("RGB", (1920, 1080), (255, 255, 255))
    arr = np.asarray(img).copy()
    # 밝은 회색 그라데이션 (lum 215~245 — 잉크 임계(205) 위)
    grad = np.linspace(215, 245, 1920).astype(np.uint8)
    arr[200:400, :, :] = grad[None, :, None]
    arr[600:640, 300:1600, :] = 10  # 검은 잉크 선
    src = tmp_path / "bg.png"
    Image.fromarray(arr).save(src)

    res = separate_ink(src, tmp_path / "ink.png", tmp_path / "flat.png")
    alpha = np.asarray(Image.open(tmp_path / "ink.png"))[:, :, 3].astype(float) / 255
    assert alpha.shape == (1080, 1920)
    assert alpha[620, 900] == pytest.approx(1.0)      # 선 위 — 완전 잉크
    assert alpha[300, 900] == pytest.approx(0.0)      # 그라데이션 — 잉크 아님
    assert alpha[100, 100] == pytest.approx(0.0)      # 흰 여백
    # flat: 잉크만 흰 배경
    flat = np.asarray(Image.open(tmp_path / "flat.png"))
    assert (flat[100, 100] == (255, 255, 255)).all()
    assert flat[620, 900].mean() < 30
    assert 0 < res["inkFraction"] < 0.1


def test_separate_ink_contain_no_crop(tmp_path):
    """정사각 입력 → contain + 종이색 패딩 (좌우 패딩, 잘림 금지)."""
    src = tmp_path / "sq.png"
    Image.new("RGB", (1000, 1000), (0, 0, 0)).save(src)
    separate_ink(src, tmp_path / "ink.png", tmp_path / "flat.png")
    ink = np.asarray(Image.open(tmp_path / "ink.png"))
    assert ink.shape[:2] == (1080, 1920)
    assert ink[540, 10, 3] == 0                        # 좌측 패딩 — 잉크 아님
    rgb = ink[540, 10, :3]
    assert tuple(rgb) == PEN_PAPER                     # 패딩은 종이색
    assert ink[540, 960, 3] == 255                     # 중앙 — 검은 잉크


def test_pen_route_params_draw_end():
    """pen 프로파일 draw_end = duration×0.35, 이후 +8에 펜 소멸, 정밀 파라미터."""
    p = buildmod.pen_route_params(300, seed=7)
    assert p.draw_end == 105                # 300×0.35
    assert p.pen_invisible_after == 113     # draw_end+8
    assert (p.analyze_scale, p.contour_width, p.rdp_eps) == (1.5, 18, 1.5)
    assert (p.max_len, p.min_route_len, p.seal_width, p.seal_step) == (300, 12, 24, 18)
    assert buildmod.pen_route_params(451, seed=1).draw_end == round(451 * 0.35)


def test_pen_route_params_can_start_at_zero_after_an_opening_poster():
    p = buildmod.pen_route_params(300, seed=7, draw_start=0)
    assert p.draw_start == 0
    assert p.draw_end == 105

"""routes.py 테스트 — TC-3.1(검은 원), TC-3.E1(백지)."""
import logging
import math

import numpy as np
import pytest
from PIL import Image, ImageDraw

from brushvid.routes import RouteParams, _raster, _seal_residual_components, generate_routes

W, H = 1920, 1080
CX, CY, R = 960, 540, 300
RING_W = 24  # 원 외곽선 두께


@pytest.fixture()
def circle_png(tmp_path):
    """흰 배경에 검은 원 외곽선 PNG."""
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([CX - R, CY - R, CX + R, CY + R], outline=(0, 0, 0), width=RING_W)
    p = tmp_path / "circle.png"
    img.save(p)
    return p


def test_tc_3_1_black_circle_strokes(circle_png):
    """TC-3.1: 검은 원 → 스트로크 ≥1, 모든 점이 원 경계 ±밴드폭 내."""
    params = RouteParams(duration=300, draw_start=8, draw_end=220, pen_invisible_after=228, seed=1)
    data = generate_routes(circle_png, params)
    strokes = data["strokes"]
    assert len(strokes) >= 1
    assert data["meta"]["coverage"] >= 0.95

    # 밴드폭: 링 두께 절반 + seal/contour 최대 반폭 + 분석 해상도 오차 여유
    band = RING_W / 2 + max(params.contour_width, params.seal_width) / 2 + 2 * params.analyze_scale
    for s in strokes:
        for x, y in s["points"]:
            dist = math.hypot(x - CX, y - CY)
            assert abs(dist - R) <= band, f"{s['id']} 점 ({x},{y}) 가 경계 밴드 밖 (dist={dist:.1f})"


def test_tc_3_1_timing_monotonic(circle_png):
    """start/end 가 drawStart~drawEnd 안에서 단조 증가."""
    params = RouteParams(duration=300, draw_start=8, draw_end=220, pen_invisible_after=228)
    data = generate_routes(circle_png, params)
    prev_end = 0.0
    for s in data["strokes"]:
        assert s["start"] < s["end"]
        assert s["start"] >= prev_end - 1e-6
        prev_end = s["end"]
    assert data["strokes"][0]["start"] >= 8
    assert data["strokes"][-1]["end"] <= 220 + 0.01
    m = data["meta"]
    assert (m["drawStart"], m["drawEnd"], m["penInvisibleAfter"]) == (8, 220, 228)


def test_tc_3_e1_blank_image(tmp_path, caplog):
    """TC-3.E1: 완전 백지 → 빈 strokes + 경고, 크래시 금지."""
    p = tmp_path / "blank.png"
    Image.new("RGB", (W, H), (255, 255, 255)).save(p)
    with caplog.at_level(logging.WARNING, logger="brushvid.routes"):
        data = generate_routes(p, RouteParams())
    assert data["strokes"] == []
    assert data["meta"]["routeCount"] == 0
    assert any("백지" in r.message or "콘텐츠" in r.message for r in caplog.records)


def test_routes_meta_shape(circle_png):
    """산출 routes 가 RoutesData 형태(meta 필수 필드 + strokes 필드)와 일치."""
    data = generate_routes(circle_png, RouteParams())
    meta = data["meta"]
    for key in ("image", "width", "height", "fps", "durationInFrames",
                "drawStart", "drawEnd", "penInvisibleAfter", "routeCount",
                "coverage", "contentPixels", "seed"):
        assert key in meta
    s = data["strokes"][0]
    for key in ("id", "kind", "width", "start", "end", "points"):
        assert key in s
    assert isinstance(s["points"][0], list) and len(s["points"][0]) == 2


def test_residual_component_seals_cover_sparse_ink_without_zero_length_paths():
    """band 사이의 미세 잉크 섬도 non-zero round-cap route로 완결한다."""
    missing = np.zeros((40, 60), dtype=bool)
    missing[4, 5] = True
    missing[31, 48] = True
    missing[18:21, 27] = True

    seals = _seal_residual_components(missing, up=1.0)
    covered = _raster(seals, 60, 40, up=1.0)

    assert len(seals) == 3
    assert all(route.pts[0] != route.pts[-1] for route in seals)
    assert (covered & missing).sum() == missing.sum()

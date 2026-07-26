"""layout.py 테스트 — TC-3.4(겹침 hard-fail) + 빈영역/자동배치."""
from PIL import Image, ImageDraw

from brushvid.layout import find_empty_regions, place_widgets, validate_layout


def _bg(tmp_path, with_content=True):
    """우측 절반에 그림이 있는 1920×1080 배경."""
    img = Image.new("RGB", (1920, 1080), (251, 250, 246))
    if with_content:
        d = ImageDraw.Draw(img)
        d.rectangle([1100, 250, 1750, 900], fill=(70, 90, 60))
    p = tmp_path / "bg.png"
    img.save(p)
    return p


def test_tc_3_4_widget_overlap_hard_fail():
    """TC-3.4: 위젯 2개가 서로 겹치면 hard-fail."""
    widgets = [
        {"type": "stat", "x": 200, "y": 300, "w": 400, "h": 200},
        {"type": "donut", "x": 350, "y": 350, "w": 400, "h": 200},  # 겹침
    ]
    res = validate_layout(widgets)
    assert not res.ok
    assert any("위젯 겹침" in f for f in res.fails)


def test_tc_3_4_title_overlap_hard_fail():
    """TC-3.4: 위젯이 타이틀존과 겹치면 hard-fail."""
    tt = {"lines": ["타이틀 한 줄"], "x": 110, "y": 74, "width": 700}
    widgets = [{"type": "stat", "x": 120, "y": 100, "w": 400, "h": 200}]
    res = validate_layout(widgets, top_title=tt)
    assert not res.ok
    assert any("타이틀존" in f for f in res.fails)


def test_subtitle_zone_and_edge_margin():
    """자막존 침범과 가장자리 여백(<90px) 도 hard-fail."""
    widgets = [{"type": "stat", "x": 400, "y": 850, "w": 400, "h": 150}]  # 자막존 침범
    res = validate_layout(widgets, has_cues=True)
    assert any("자막존" in f for f in res.fails)

    widgets2 = [{"type": "stat", "x": 20, "y": 300, "w": 300, "h": 180}]  # 좌측 여백 20px
    res2 = validate_layout(widgets2)
    assert any("가장자리" in f for f in res2.fails)


def test_valid_layout_passes():
    """겹침 없는 배치는 통과."""
    tt = {"lines": ["타이틀"], "x": 110, "y": 74, "width": 700}
    widgets = [
        {"type": "stat", "x": 150, "y": 420, "w": 380, "h": 180},
        {"type": "donut", "x": 150, "y": 640, "w": 380, "h": 180},
    ]
    res = validate_layout(widgets, top_title=tt, has_cues=True)
    assert res.ok, res.fails


def test_find_empty_regions_avoids_content(tmp_path):
    """빈 영역 박스는 콘텐츠 사각형과 겹치지 않는다."""
    bg = _bg(tmp_path)
    boxes = find_empty_regions(bg, count=3)
    assert boxes
    for b in boxes:
        # 콘텐츠(1100..1750, 250..900)와 교차 없음
        ix = max(0, min(b["x"] + b["w"], 1750) - max(b["x"], 1100))
        iy = max(0, min(b["y"] + b["h"], 900) - max(b["y"], 250))
        assert ix * iy == 0, f"빈 박스가 콘텐츠와 겹침: {b}"


def test_place_widgets_then_validate(tmp_path):
    """자동 배치 결과가 겹침 검증을 통과한다 (배치→검증 왕복)."""
    bg = _bg(tmp_path)
    tt = {"lines": ["겨울 소나무"], "x": 110, "y": 74, "width": 620}
    widgets = [{"type": "stat", "label": "A"}, {"type": "donut", "label": "B"}]
    placed = place_widgets(bg, widgets, top_title=tt, has_cues=True)
    assert all(k in w for w in placed for k in ("x", "y", "w", "h"))
    res = validate_layout(placed, top_title=tt, has_cues=True, image_path=bg)
    assert res.ok, res.fails

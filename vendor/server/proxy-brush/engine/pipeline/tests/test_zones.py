"""매크로 존 그룹핑 테스트 — 존 분리·순서 / False 동일성 / 엣지."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

from brushvid.routes import RouteParams, generate_routes

FIXTURES = Path(__file__).parent / "fixtures"


def _two_shapes(tmp_path):
    """좌상단 사각형 + 우하단 원 — 서로 멀리 떨어진 2오브젝트."""
    img = Image.new("RGB", (1920, 1080), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([200, 150, 560, 430], outline=(0, 0, 0), width=20)   # 존1 (좌상)
    d.ellipse([1300, 650, 1720, 980], outline=(0, 0, 0), width=20)   # 존2 (우하)
    p = tmp_path / "two.png"
    img.save(p)
    return p


def test_two_shapes_zone_split_and_order(tmp_path):
    """2도형 → zoneCount 2, 존1(좌상) 스트로크 전부가 존2보다 먼저."""
    data = generate_routes(_two_shapes(tmp_path),
                           RouteParams(duration=300, draw_end=105, group_by_zone=True, seed=3))
    assert data["meta"]["zoneCount"] == 2
    strokes = data["strokes"]
    assert len(strokes) >= 2

    def which_zone(s):
        xs = [pt[0] for pt in s["points"]]
        ys = [pt[1] for pt in s["points"]]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        return 1 if (cx < 960 and cy < 540) else 2

    zones_seq = [which_zone(s) for s in strokes]
    assert zones_seq[0] == 1                      # 최좌상 존 시작
    switch = zones_seq.index(2)
    assert all(z == 1 for z in zones_seq[:switch])
    assert all(z == 2 for z in zones_seq[switch:])  # 존1 완성 후 존2 — 교차 없음
    # 타이밍도 존 단위로 비겹침
    z1_end = max(s["end"] for s in strokes[:switch])
    z2_start = min(s["start"] for s in strokes[switch:])
    assert z2_start >= z1_end - 1e-6


def test_group_by_zone_false_byte_identical():
    """group_by_zone=False 경로는 변경 전 baseline 과 바이트 동일 (brush 회귀 0)."""
    data = generate_routes(FIXTURES / "circle.png",
                           RouteParams(duration=300, draw_start=8, draw_end=220,
                                       pen_invisible_after=228, seed=42))
    data["meta"]["image"] = "circle.png"  # baseline 과 동일한 경로 정규화
    baseline = (FIXTURES / "circle-routes-baseline.json").read_text(encoding="utf-8")
    assert json.dumps(data) == baseline
    assert "zoneCount" not in data["meta"]  # False 경로 meta 불변


def test_single_zone_and_blank(tmp_path):
    """전부 붙은 그림 → 존 1개 / 백지 → 존 0개, 크래시 없음."""
    img = Image.new("RGB", (1920, 1080), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([660, 240, 1260, 840], outline=(0, 0, 0), width=24)
    one = tmp_path / "one.png"
    img.save(one)
    data = generate_routes(one, RouteParams(group_by_zone=True))
    assert data["meta"]["zoneCount"] == 1
    assert data["meta"]["coverage"] >= 0.95

    blank = tmp_path / "blank.png"
    Image.new("RGB", (1920, 1080), (255, 255, 255)).save(blank)
    data2 = generate_routes(blank, RouteParams(group_by_zone=True))
    assert data2["strokes"] == []
    assert data2["meta"]["zoneCount"] == 0

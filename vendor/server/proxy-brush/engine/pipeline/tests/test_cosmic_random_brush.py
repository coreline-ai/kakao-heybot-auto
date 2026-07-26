from __future__ import annotations

import hashlib
import json

from PIL import Image

from brushvid.cosmic_random_routes import (
    CosmicRandomRouteParams,
    generate_cosmic_random_routes,
    route_report,
)
from brushvid.qa import write_cosmic_random_brush_report


def _hash(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def test_cosmic_random_routes_golden_contract(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (1920, 1080), (1, 2, 13)).save(image)
    params = CosmicRandomRouteParams(seed=260712)
    first = generate_cosmic_random_routes(image, image_rel="fixture/source.png", params=params)
    second = generate_cosmic_random_routes(image, image_rel="fixture/source.png", params=params)
    meta = first["meta"]

    assert _hash(first) == _hash(second)
    assert meta["family"] == "free-random-touch"
    assert meta["baseStrokeCount"] == 36
    assert 1 <= meta["coverageStrokeCount"] <= 20
    assert meta["maskCoverage"] >= 0.991
    assert meta["brushWidthRange"][0] >= 230
    assert meta["brushWidthRange"][1] <= 365
    assert meta["meanCenterJump"] >= 650
    assert meta["maxCenterJump"] >= 1200
    assert meta["drawEnd"] <= 210
    assert len(first["strokes"]) == meta["strokeCount"]
    assert all(len(point) == 3 for stroke in first["strokes"] for point in stroke["points"])

    report = route_report(first)
    assert report["strokeCount"] == meta["strokeCount"]
    assert report["maskCoverage"] == meta["maskCoverage"]


def test_cosmic_random_routes_other_seeds_keep_quality_contract(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (1920, 1080), (1, 2, 13)).save(image)
    hashes = set()
    for seed in (260713, 260714, 260715):
        data = generate_cosmic_random_routes(
            image,
            image_rel="fixture/source.png",
            params=CosmicRandomRouteParams(seed=seed),
        )
        meta = data["meta"]
        hashes.add(_hash(data))
        assert meta["maskCoverage"] >= 0.991
        assert 1 <= meta["coverageStrokeCount"] <= 20
        assert meta["brushWidthRange"][0] >= 230
        assert meta["brushWidthRange"][1] <= 365
        assert meta["meanCenterJump"] >= 650
        assert meta["maxCenterJump"] >= 1200
        assert meta["drawEnd"] <= 210
    assert len(hashes) == 3


def test_cosmic_random_routes_v02_seed_matrix_and_content_coverage(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (1920, 1080), (24, 36, 72)).save(image)
    for seed in (260712, 260749, 260786, 260823, 260860, 260897):
        data = generate_cosmic_random_routes(
            image, image_rel="fixture/source.png",
            params=CosmicRandomRouteParams(seed=seed),
        )
        meta = data["meta"]
        assert meta["contentAnalysisVersion"] == "luma-chroma-v1"
        assert meta["visibleContentFraction"] == 1.0
        assert meta["visibleContentCoverage"] >= 0.985
        assert meta["maskCoverage"] >= 0.991
        assert meta["brushWidthRange"][1] <= 365


def test_cosmic_random_routes_rejects_wrong_image_size(tmp_path):
    image = tmp_path / "small.png"
    Image.new("RGB", (320, 180), (0, 0, 0)).save(image)
    try:
        generate_cosmic_random_routes(image, image_rel="fixture/small.png")
    except ValueError as exc:
        assert "이미지 크기" in str(exc)
    else:
        raise AssertionError("wrong-size image must fail")


def test_cosmic_random_routes_rejects_fully_transparent_image(tmp_path):
    image = tmp_path / "transparent.png"
    Image.new("RGBA", (1920, 1080), (0, 0, 0, 0)).save(image)
    try:
        generate_cosmic_random_routes(image, image_rel="fixture/transparent.png")
    except ValueError as exc:
        assert "완전히 투명" in str(exc)
    else:
        raise AssertionError("fully transparent image must fail")


def test_cosmic_random_routes_rejects_coverage_shortcut(tmp_path):
    image = tmp_path / "source.png"
    Image.new("RGB", (1920, 1080), (0, 0, 0)).save(image)
    params = CosmicRandomRouteParams(seed=260712, target_coverage=0.99999, max_supplements=0)
    try:
        generate_cosmic_random_routes(image, image_rel="fixture/source.png", params=params)
    except ValueError as exc:
        assert "보완 터치 상한" in str(exc)
    else:
        raise AssertionError("unreachable target must fail instead of widening brush")


def test_cosmic_random_brush_qa_blocks_width_regression(tmp_path):
    good = {
        "family": "free-random-touch", "strokeCount": 46,
        "baseStrokeCount": 36, "coverageStrokeCount": 10,
        "brushWidthRange": [232.8, 364.2], "maskCoverage": 0.9929,
        "meanCenterJump": 852.01, "maxCenterJump": 1928.28,
        "drawStart": 37, "drawEnd": 207.31, "brushInvisibleAfter": 214,
        "settleStart": 216, "settleEnd": 232, "deterministic": True,
    }
    passed = write_cosmic_random_brush_report(
        tmp_path / "pass.json", project_id="golden",
        scenes=[{"sceneId": "scene-01", "meta": good}],
    )
    assert passed["pass"] is True

    widened = {**good, "brushWidthRange": [345, 547.5]}
    failed = write_cosmic_random_brush_report(
        tmp_path / "fail.json", project_id="regression",
        scenes=[{"sceneId": "scene-01", "meta": widened}],
    )
    assert failed["pass"] is False


def test_cosmic_random_brush_v02_qa_requires_visible_content_coverage(tmp_path):
    base = {
        "family": "free-random-touch", "strokeCount": 46,
        "baseStrokeCount": 36, "coverageStrokeCount": 10,
        "brushWidthRange": [232.8, 364.2], "maskCoverage": 0.9929,
        "visibleContentFraction": 0.4, "visibleContentCoverage": 0.991,
        "meanCenterJump": 852.01, "maxCenterJump": 1928.28,
        "drawStart": 37, "drawEnd": 207.31, "brushInvisibleAfter": 214,
        "settleStart": 216, "settleEnd": 232, "deterministic": True,
    }
    scenes = [{"sceneId": f"scene-{i + 1:02d}", "meta": dict(base)} for i in range(6)]
    passed = write_cosmic_random_brush_report(
        tmp_path / "v02-pass.json", project_id="v02", scenes=scenes)
    assert passed["pass"] is True
    scenes[4]["meta"]["visibleContentCoverage"] = 0.97
    failed = write_cosmic_random_brush_report(
        tmp_path / "v02-fail.json", project_id="v02-regression", scenes=scenes)
    assert failed["pass"] is False
    assert failed["scenes"][4]["pass"] is False

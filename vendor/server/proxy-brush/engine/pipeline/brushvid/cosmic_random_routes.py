"""결정적 자유 랜덤 브러시 routes 생성기.

승인된 cosmic-dark-pilot의 동작 계약을 본선화한다.
기본 터치는 의미 윤곽을 추적하지 않고 화면 전체에 분산되며, 커버리지가
부족하면 붓 폭을 키우지 않고 동일 폭 범위의 보완 터치를 추가한다.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import distance_transform_edt


@dataclass(frozen=True)
class CosmicRandomRouteParams:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration: int = 300
    seed: int = 260712
    draw_start: float = 37.0
    base_rows: int = 5
    base_cols: int = 6
    extra_subject_touches: int = 6
    brush_min: float = 230.0
    brush_max: float = 365.0
    length_min: float = 320.0
    length_max: float = 610.0
    target_coverage: float = 0.991
    max_supplements: int = 20
    settle_start: float = 216.0
    settle_end: float = 232.0
    brush_invisible_after: float = 214.0

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("cosmic random routes: width/height는 양수여야 함")
        if self.fps != 30 or self.duration != 300:
            raise ValueError("cosmic-random-brush v0.1은 30fps/300f만 지원")
        if self.base_rows <= 0 or self.base_cols <= 0:
            raise ValueError("base_rows/base_cols는 양수여야 함")
        if not (0 < self.brush_min <= self.brush_max):
            raise ValueError("brush_min <= brush_max 양수 범위가 필요")
        if not (0.5 <= self.target_coverage <= 1.0):
            raise ValueError("target_coverage는 0.5~1.0")
        if not (0 <= self.max_supplements <= 100):
            raise ValueError("max_supplements는 0~100")


def pressure(t: float) -> float:
    """붓의 부드러운 진입, 강한 중간, 들리는 끝 압력."""
    return 0.18 + 0.82 * math.sin(math.pi * t) ** 0.48


def make_points(cx: float, cy: float, angle: float, length: float, curve: float,
                *, count: int = 14, hand_scale: float = 1.0) -> list[list[float]]:
    dx, dy = math.cos(angle), math.sin(angle)
    nx, ny = -dy, dx
    points: list[list[float]] = []
    for i in range(count):
        t = i / (count - 1)
        along = (t - 0.5) * length
        bend = math.sin((t - 0.5) * math.pi) * curve
        hand = math.sin(t * math.pi * 2.0 + cx * 0.007) * 5.0 * hand_scale
        x = cx + dx * along + nx * (bend + hand)
        y = cy + dy * along + ny * (bend + hand)
        points.append([round(x, 1), round(y, 1), round(pressure(t), 4)])
    return points


def build_route_mask(strokes: list[dict], width: int, height: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for stroke in strokes:
        points = stroke["points"]
        brush_width = float(stroke["width"])
        for i in range(1, len(points)):
            p0, p1 = points[i - 1], points[i]
            local_width = max(8, int(brush_width * (p0[2] + p1[2]) * 0.5))
            draw.line((p0[0], p0[1], p1[0], p1[1]), fill=230, width=local_width)
    return mask


def mask_coverage(mask: Image.Image) -> float:
    return sum(mask.histogram()[9:]) / (mask.width * mask.height)


def visible_content_metrics(image_path: str | Path, route_mask: Image.Image) -> tuple[float, float]:
    """Return visible-content fraction and route coverage over that content.

    Dark space backgrounds are intentionally excluded where both luminance and
    chroma are negligible. This supplements, but never replaces, the full-canvas
    0.991 mask contract.
    """
    rgba = np.asarray(Image.open(image_path).convert("RGBA"), dtype=np.float32)
    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3] > 8
    luma = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    visible = alpha & ((luma >= 12) | (chroma >= 8))
    visible_count = int(visible.sum())
    if visible_count == 0:
        raise ValueError("cosmic random routes: 가시 콘텐츠 영역을 찾을 수 없음")
    painted = np.asarray(route_mask) >= 9
    return float(visible.mean()), float(painted[visible].mean())


def _stroke(rng: random.Random, index: int, cx: float, cy: float, cursor: float,
            params: CosmicRandomRouteParams, previous_angle: float | None,
            *, supplement: bool = False) -> tuple[dict, float, float]:
    angle = rng.uniform(-math.pi, math.pi)
    if previous_angle is not None and abs(math.sin(angle - previous_angle)) < 0.28:
        angle += rng.choice((-1, 1)) * rng.uniform(0.45, 0.9)
    length = rng.uniform(params.length_min, params.length_max)
    width = rng.uniform(params.brush_min, params.brush_max)
    duration = rng.uniform(2.2, 2.9) if supplement else rng.uniform(2.8, 3.8)
    gap = rng.uniform(0.25, 0.55) if supplement else rng.uniform(0.35, 0.9)
    coordinate_scale = params.width / 1920
    stroke = {
        "id": f"touch-{index:02d}",
        "kind": "random-touch",
        "width": round(width, 1),
        "start": round(cursor, 2),
        "end": round(cursor + duration, 2),
        "opacity": round(rng.uniform(0.78, 0.98), 3),
        "dryness": round(rng.uniform(0.16, 0.34), 3),
        "points": make_points(cx, cy, angle, length, rng.uniform(-42, 42) * coordinate_scale,
                              hand_scale=coordinate_scale),
    }
    return stroke, cursor + duration + gap, angle


def _base_centers(rng: random.Random, p: CosmicRandomRouteParams) -> list[tuple[float, float]]:
    centers: list[tuple[float, float]] = []
    jitter_x = p.width * (105 / 1920)
    jitter_y = p.height * (62 / 1080)
    for row in range(p.base_rows):
        for col in range(p.base_cols):
            centers.append((
                (col + 0.5) * (p.width / p.base_cols) + rng.uniform(-jitter_x, jitter_x),
                (row + 0.5) * (p.height / p.base_rows) + rng.uniform(-jitter_y, jitter_y),
            ))
    rng.shuffle(centers)
    # 승인 데모와 동일하게 우하단 밝은 피사체 영역에 여섯 터치를 더한다.
    subject_regions = [
        (1180 / 1920, 1840 / 1920, 410 / 1080, 850 / 1080),
        (520 / 1920, 1450 / 1920, 650 / 1080, 1010 / 1080),
    ]
    first = min(4, p.extra_subject_touches)
    for _ in range(first):
        x0, x1, y0, y1 = subject_regions[0]
        centers.append((rng.uniform(x0, x1) * p.width, rng.uniform(y0, y1) * p.height))
    for _ in range(p.extra_subject_touches - first):
        x0, x1, y0, y1 = subject_regions[1]
        centers.append((rng.uniform(x0, x1) * p.width, rng.uniform(y0, y1) * p.height))
    return centers


def generate_cosmic_random_routes(image_path: str | Path, *, image_rel: str,
                                  params: CosmicRandomRouteParams | None = None) -> dict:
    p = params or CosmicRandomRouteParams()
    p.validate()
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    with Image.open(image_path) as im:
        if im.size != (p.width, p.height):
            raise ValueError(
                f"cosmic random routes: 이미지 크기 {im.size} != 캔버스 {(p.width, p.height)}")
        if "A" in im.getbands() and im.getchannel("A").getbbox() is None:
            raise ValueError("cosmic random routes: 완전히 투명한 이미지는 사용할 수 없음")

    rng = random.Random(p.seed)
    strokes: list[dict] = []
    cursor = p.draw_start
    previous_angle: float | None = 0.0
    for cx, cy in _base_centers(rng, p):
        stroke, cursor, previous_angle = _stroke(
            rng, len(strokes) + 1, cx, cy, cursor, p, previous_angle)
        strokes.append(stroke)
    base_count = len(strokes)

    coverage_rng = random.Random(p.seed + 1)
    while True:
        mask = build_route_mask(strokes, p.width, p.height)
        coverage = mask_coverage(mask)
        if coverage >= p.target_coverage:
            break
        supplements = len(strokes) - base_count
        if supplements >= p.max_supplements:
            raise ValueError(
                f"cosmic random routes: coverage {coverage:.5f} < {p.target_coverage:.5f}, "
                f"보완 터치 상한 {p.max_supplements} 도달")
        uncovered = np.asarray(mask) < 9
        distance = distance_transform_edt(uncovered)
        cy, cx = np.unravel_index(np.argmax(distance), distance.shape)
        stroke, cursor, previous_angle = _stroke(
            coverage_rng, len(strokes) + 1, float(cx), float(cy), cursor, p,
            None, supplement=True)
        strokes.append(stroke)

    draw_end = float(strokes[-1]["end"])
    timing_scale = 1.0
    # 보완 터치가 많은 seed도 승인된 10초 씬의 완료 시점을 넘기지 않게 시간만
    # 균등 압축한다. 좌표·폭·순서·강모 geometry는 그대로이며 scene-01에는 적용되지 않는다.
    if draw_end > 209.5:
        timing_scale = (209.5 - p.draw_start) / (draw_end - p.draw_start)
        for stroke in strokes:
            stroke["start"] = round(p.draw_start + (float(stroke["start"]) - p.draw_start) * timing_scale, 2)
            stroke["end"] = round(p.draw_start + (float(stroke["end"]) - p.draw_start) * timing_scale, 2)
        draw_end = float(strokes[-1]["end"])
    if draw_end > p.brush_invisible_after:
        raise ValueError(
            f"cosmic random routes: drawEnd {draw_end:.2f} > brushInvisibleAfter {p.brush_invisible_after:.2f}")
    centers = [s["points"][len(s["points"]) // 2] for s in strokes]
    jumps = [math.hypot(centers[i][0] - centers[i - 1][0],
                        centers[i][1] - centers[i - 1][1]) for i in range(1, len(centers))]
    visible_fraction, visible_coverage = visible_content_metrics(image_path, mask)
    result = {
        "meta": {
            "family": "free-random-touch",
            "image": image_rel,
            "width": p.width,
            "height": p.height,
            "fps": p.fps,
            "durationInFrames": p.duration,
            "drawStart": float(strokes[0]["start"]),
            "drawEnd": draw_end,
            "settleStart": p.settle_start,
            "settleEnd": p.settle_end,
            "brushInvisibleAfter": p.brush_invisible_after,
            "strokeCount": len(strokes),
            "baseStrokeCount": base_count,
            "coverageStrokeCount": len(strokes) - base_count,
            "targetMaskCoverage": p.target_coverage,
            "maskCoverage": round(coverage, 6),
            "contentAnalysisVersion": "luma-chroma-v1",
            "visibleContentFraction": round(visible_fraction, 6),
            "visibleContentCoverage": round(visible_coverage, 6),
            "brushWidthRange": [min(s["width"] for s in strokes), max(s["width"] for s in strokes)],
            "meanCenterJump": round(sum(jumps) / len(jumps), 2),
            "maxCenterJump": round(max(jumps), 2),
            "seed": p.seed,
            "deterministic": True,
            "timingScale": round(timing_scale, 6),
        },
        "strokes": strokes,
    }
    return result


def write_cosmic_random_routes(data: dict, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def route_report(data: dict) -> dict:
    m = data["meta"]
    return {
        "family": m["family"],
        "strokeCount": m["strokeCount"],
        "baseStrokeCount": m["baseStrokeCount"],
        "coverageStrokeCount": m["coverageStrokeCount"],
        "brushWidthRange": m["brushWidthRange"],
        "seed": m["seed"],
        "drawStart": m["drawStart"],
        "drawEnd": m["drawEnd"],
        "meanCenterJump": m["meanCenterJump"],
        "maxCenterJump": m["maxCenterJump"],
        "maskCoverage": m["maskCoverage"],
        "visibleContentFraction": m["visibleContentFraction"],
        "visibleContentCoverage": m["visibleContentCoverage"],
    }

"""layout.py — 빈 영역 탐지 + 위젯 자동 배치 + 겹침 검증.

규칙: 콘텐츠(배경 그림) + 타이틀존 + 자막존을 제외한 "가장 큰 빈 사각형"에 위젯을 배치한다.
검증은 UI 겹침(위젯↔위젯/자막존/타이틀존, 화면 이탈, 가장자리 여백<90px)을 hard-fail 로 본다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

log = logging.getLogger(__name__)

W, H = 1920, 1080
SUBTITLE_ZONE = {"x": 280.0, "y": 884.0, "w": 1360.0, "h": 150.0}  # 하단 자막 pill 보수적 예약
WIDGET_EDGE_MIN = 90.0  # 위젯-화면 가장자리 최소 여백(px)

# 세로(9:16) 세이프존 — 쇼츠 UI/자막 밴드 (canvas 가 세로일 때만 적용)
PORTRAIT_TOP_SAFE = 120.0      # 상단: topTitle/위젯 y ≥ 120
PORTRAIT_SUBTITLE_BAND = 290.0  # 하단: 자막 밴드 (y > H-290 침범 금지)

Rect = dict[str, float]  # {x, y, w, h}


def rect(x: float, y: float, w: float, h: float) -> Rect:
    return {"x": float(x), "y": float(y), "w": float(w), "h": float(h)}


def _inter(a: Rect, b: Rect) -> float:
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ix * iy


def content_mask(image_path: str | Path, lum_thresh: float = 237.0, sat_thresh: float = 12.0,
                 min_blob: int = 600, dilate: int = 0,
                 size: tuple[int, int] = (W, H)) -> np.ndarray:
    """캔버스(기본 1920×1080) 콘텐츠 마스크. 작은 잡티는 제거하고 필요 시 dilate."""
    img = Image.open(image_path).convert("RGB").resize(size)
    arr = np.asarray(img).astype(int)
    lum = arr.mean(2)
    sat = arr.max(2) - arr.min(2)
    mask = (lum < lum_thresh) | (sat > sat_thresh)
    lbl, n = ndimage.label(mask)
    if n:
        sizes = ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))
        keep = np.zeros(n + 1, dtype=bool)
        keep[1:] = sizes >= min_blob
        mask = keep[lbl]
    if dilate > 0:
        mask = ndimage.binary_dilation(mask, iterations=dilate)
    return mask


def largest_rect(free: np.ndarray) -> tuple[int, int, int, int, int]:
    """최대 넓이 all-True 직사각형 (히스토그램법). 반환: area, x0, y0, x1, y1 (inclusive)."""
    h, w = free.shape
    height = np.zeros(w, dtype=int)
    best = (0, 0, 0, 0, 0)
    for r in range(h):
        height = np.where(free[r], height + 1, 0)
        stack: list[tuple[int, int]] = []
        for c in range(w + 1):
            cur = height[c] if c < w else 0
            start = c
            while stack and stack[-1][1] > cur:
                s, hh = stack.pop()
                area = hh * (c - s)
                if area > best[0]:
                    best = (area, s, r - hh + 1, c - 1, r)
                start = s
            stack.append((start, cur))
    return best


def top_title_rect(tt: dict) -> Rect:
    """TopTitle 실제 표시 영역과 유사한 보수적 예약 박스."""
    tx = float(tt.get("x", 110))
    ty = float(tt.get("y", 74))
    width = float(tt.get("width", 700))
    fs = float(tt.get("fontSize", 60))
    kfs = float(tt.get("kickerFontSize", 20))
    nlines = len(tt.get("lines", []) or [])
    wash = tt.get("wash", True) is not False
    pad_x = 24 if wash else 0
    pad_y = 18 if wash else 0
    kicker_h = (kfs * 1.25 + 16) if tt.get("kicker") else 0
    body_h = nlines * fs * 1.14
    return rect(tx - 16, ty - 14, width + pad_x + 32, pad_y + kicker_h + body_h + 28)


def _occupied_to_grid(occ: np.ndarray, cell: int) -> np.ndarray:
    """픽셀 occupied 마스크 → 셀 단위 free 그리드."""
    gh, gw = H // cell, W // cell
    free = np.ones((gh, gw), dtype=bool)
    for r in range(gh):
        for c in range(gw):
            if occ[r * cell:(r + 1) * cell, c * cell:(c + 1) * cell].any():
                free[r, c] = False
    return free


def find_empty_regions(image_path: str | Path, count: int = 4, *, cell: int = 16,
                       margin: int = 16, min_blob: int = 600, min_w: int = 300,
                       min_h: int = 180, pad: int = 22, edge: int = 48) -> list[Rect]:
    """콘텐츠 빈 여백에서 위젯이 들어갈 큰 사각형들을 반복 검출."""
    content = content_mask(image_path, min_blob=min_blob, dilate=margin)
    free = _occupied_to_grid(content, cell)
    em = max(1, edge // cell)
    free[:em, :] = False
    free[-em:, :] = False
    free[:, :em] = False
    free[:, -em:] = False

    boxes: list[Rect] = []
    for _ in range(count):
        area, x0, y0, x1, y1 = largest_rect(free)
        if area == 0:
            break
        px0, py0 = x0 * cell + pad, y0 * cell + pad
        px1, py1 = (x1 + 1) * cell - pad, (y1 + 1) * cell - pad
        if (px1 - px0) >= min_w and (py1 - py0) >= min_h:
            boxes.append(rect(px0, py0, px1 - px0, py1 - py0))
        free[y0:y1 + 1, x0:x1 + 1] = False  # 다음 검출을 위해 제거
    return boxes


def place_widgets(image_path: str | Path, widgets: list[dict], *, top_title: dict | None = None,
                  has_cues: bool = False, cell: int = 16, margin: int = 18, min_blob: int = 600,
                  edge: int = 130, gap: int = 24, pad: int = 16, min_card_w: int = 210) -> list[dict]:
    """위젯 목록에 x/y/w/h 를 계산해 덮어쓴다 (빈영역 우선, 부족 시 겹침 허용 폴백)."""
    n = len(widgets)
    if n == 0:
        return widgets

    occ = content_mask(image_path, min_blob=min_blob, dilate=margin).copy()
    if top_title:
        t = top_title_rect(top_title)
        y0, x0 = max(0, int(t["y"])), max(0, int(t["x"]))
        occ[y0:min(H, int(t["y"] + t["h"])), x0:min(W, int(t["x"] + t["w"]))] = True
    if has_cues:
        z = SUBTITLE_ZONE
        occ[int(z["y"]):int(z["y"] + z["h"]), int(z["x"]):int(z["x"] + z["w"])] = True
    occ[:edge, :] = True
    occ[-edge:, :] = True
    occ[:, :edge] = True
    occ[:, -edge:] = True

    free = _occupied_to_grid(occ, cell)
    area, gx0, gy0, gx1, gy1 = largest_rect(free)
    bx0, by0 = gx0 * cell + pad, gy0 * cell + pad
    bx1, by1 = (gx1 + 1) * cell - pad, (gy1 + 1) * cell - pad
    bw, bh = bx1 - bx0, by1 - by0

    # 빈 박스가 너무 작으면 → 겹침 허용 폴백(자막/타이틀만 피한 좌측 상단 밴드)
    if bw < min_card_w or bh < 150 or area == 0:
        log.warning("빈영역 부족 — 배경 겹침 허용 폴백 배치")
        bx0, by0, bx1, by1 = edge, 300, W - edge, 490
        bw, bh = bx1 - bx0, by1 - by0

    cw_row = (bw - gap * (n - 1)) / n
    if cw_row >= min_card_w:  # 가로 row 배치
        cw, ch = int(cw_row), int(min(bh, 200))
        for i, wdg in enumerate(widgets):
            wdg["x"], wdg["y"] = int(bx0 + i * (cw + gap)), int(by0)
            wdg["w"], wdg["h"] = cw, ch
    else:  # 세로 stack 배치
        ch = int((bh - gap * (n - 1)) / n)
        cw = int(min(bw, 360))
        for i, wdg in enumerate(widgets):
            wdg["x"], wdg["y"] = int(bx0), int(by0 + i * (ch + gap))
            wdg["w"], wdg["h"] = cw, ch
    return widgets


@dataclass
class LayoutResult:
    """겹침 검증 결과. fails 가 하나라도 있으면 hard-fail."""

    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fails


def validate_layout(widgets: list[dict], *, top_title: dict | None = None, has_cues: bool = False,
                    image_path: str | Path | None = None, content_max: float = 0.03,
                    widget_edge: float = WIDGET_EDGE_MIN,
                    canvas: tuple[int, int] = (W, H)) -> LayoutResult:
    """UI 겹침/침범 하드 체크. 위젯↔위젯/자막존/타이틀존, 화면 이탈, 가장자리 여백.

    배경 콘텐츠와의 겹침은 소프트 경고(warns)로만 보고한다.
    canvas 가 세로(height > width)면 상/하단 세이프존 침범도 hard-fail — 가로 경로 동작 무변경.
    """
    res = LayoutResult()
    cw, ch = canvas
    portrait = ch > cw
    boxes = [(f"widget[{i}]:{w.get('type', '?')}", rect(w["x"], w["y"], w["w"], w["h"]))
             for i, w in enumerate(widgets)]
    if portrait:  # 세로: 자막 밴드는 하단 풀폭 예약
        sub_zone = rect(0, ch - PORTRAIT_SUBTITLE_BAND, cw, PORTRAIT_SUBTITLE_BAND) if has_cues else None
    else:
        sub_zone = dict(SUBTITLE_ZONE) if has_cues else None
    title_zone = top_title_rect(top_title) if top_title else None

    # 1) 위젯 ↔ 위젯
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _inter(boxes[i][1], boxes[j][1]) > 0:
                res.fails.append(f"위젯 겹침: {boxes[i][0]} ∩ {boxes[j][0]}")
    # 2) 위젯 ↔ 자막존
    if sub_zone:
        for name, b in boxes:
            if _inter(b, sub_zone) > 0:
                res.fails.append(f"자막존 침범: {name}")
    # 3) 위젯 ↔ 타이틀존
    if title_zone:
        for name, b in boxes:
            if _inter(b, title_zone) > 0:
                res.fails.append(f"타이틀존 침범: {name}")
    # 4) 타이틀존 ↔ 자막존
    if title_zone and sub_zone and _inter(title_zone, sub_zone) > 0:
        res.fails.append("타이틀존 ↔ 자막존 겹침")
    # 5) 위젯 ↔ 그림 콘텐츠 (소프트 경고)
    if image_path is not None and boxes:
        content = content_mask(image_path, size=canvas)
        for name, b in boxes:
            x0, y0 = max(0, int(b["x"])), max(0, int(b["y"]))
            x1, y1 = min(cw, int(b["x"] + b["w"])), min(ch, int(b["y"] + b["h"]))
            if x1 > x0 and y1 > y0:
                frac = float(content[y0:y1, x0:x1].mean())
                if frac > content_max:
                    res.warns.append(f"배경 겹침(경고): {name} 아래 그림 {frac * 100:.1f}%")
    # 6) bounds + 가장자리 최소 여백
    for name, b in boxes:
        if b["x"] < 0 or b["y"] < 0 or b["x"] + b["w"] > cw or b["y"] + b["h"] > ch:
            res.fails.append(f"화면 이탈: {name}")
        elif (b["x"] < widget_edge or b["y"] < widget_edge
              or (cw - (b["x"] + b["w"])) < widget_edge or (ch - (b["y"] + b["h"])) < widget_edge):
            res.fails.append(f"가장자리 여백 부족: {name} (최소 {int(widget_edge)}px)")
    # 7) 세로 세이프존 (portrait 전용): 상단 y<120 / 하단 자막 밴드 침범
    if portrait:
        band_top = ch - PORTRAIT_SUBTITLE_BAND
        for name, b in boxes:
            if b["y"] < PORTRAIT_TOP_SAFE:
                res.fails.append(f"상단 세이프존 침범: {name} (y<{int(PORTRAIT_TOP_SAFE)})")
            if b["y"] + b["h"] > band_top:
                res.fails.append(f"하단 자막 밴드 침범: {name} (y+h>{int(band_top)})")
        if top_title is not None and float(top_title.get("y", 74)) < PORTRAIT_TOP_SAFE:
            res.fails.append(f"상단 세이프존 침범: topTitle (y<{int(PORTRAIT_TOP_SAFE)})")

    if res.fails:
        log.error("LAYOUT FAIL — %s", "; ".join(res.fails))
    return res

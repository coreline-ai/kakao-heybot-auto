"""routes.py — 이미지 1장 → 붓 드로잉 stroke routes JSON 생성.

파이프라인:
  1) 이미지 로드 → 분석 해상도로 리사이즈
  2) content mask (종이보다 어둡거나 채도가 있는 픽셀)
  3) skeletonize → node-to-node 폴리라인 추적 → RDP 단순화 → 긴 path 분할 (contour)
  4) contour 래스터화 → 미커버 픽셀에 seal 밴드(수평→수직) 생성 → 커버리지 ~100%
  5) 이동 anchor 순회 + travel/재방문 penalty 기반 순서 결정
  6) 타이밍 배정 (gap ∝ travel, draw ∝ length → [drawStart, drawEnd] 스케일)
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from skimage.morphology import closing, dilation, disk, skeletonize

log = logging.getLogger(__name__)

Point = tuple[float, float]


@dataclass
class RouteParams:
    """routes 생성 파라미터. 좌표 단위는 target px(1920×1080 기준), 내부에서 분석 px로 환산."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration: int = 300           # composition 총 프레임
    draw_start: float = 6.0
    draw_end: float | None = None          # 미지정 시 duration*0.88
    pen_invisible_after: float | None = None  # 미지정 시 duration*0.94
    seed: int = 7
    analyze_scale: float = 2.0    # target/scale 해상도로 분석
    contour_width: float = 34.0
    seal_width: float = 40.0
    seal_step: float | None = None  # 미지정 시 seal_width*0.9
    lum_thresh: float = 14.0      # 종이 대비 밝기 차 임계
    sat_thresh: float = 16.0      # 채도 임계
    rdp_eps: float = 2.0          # 분석 px 기준
    max_len: float = 560.0        # 이보다 길면 분할 (target px)
    min_route_len: float = 26.0   # 이보다 짧은 contour는 버리고 seal이 커버 (target px)
    close: int = 2                # content mask closing disk 반경
    group_by_zone: bool = False   # 매크로 존(오브젝트) 단위 순서 — pen 프로파일에서 True
    zone_merge_px: float = 12.0   # 존 병합 팽창 반경 (target px — 글자 조각은 합치고 오브젝트는 분리)


@dataclass
class _Route:
    pts: list[Point]
    width: float
    kind: str
    cx: float = 0.0
    cy: float = 0.0
    length: float = 0.0
    zone: int | None = None  # 드로잉 순서 기준 존 인덱스 (group_by_zone 경로에서만)


def rdp(points: list[Point], eps: float) -> list[Point]:
    """Ramer–Douglas–Peucker 폴리라인 단순화 (반복 구현)."""
    if len(points) < 3:
        return points
    stack = [(0, len(points) - 1)]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    while stack:
        i0, i1 = stack.pop()
        ax, ay = points[i0]
        bx, by = points[i1]
        dx, dy = bx - ax, by - ay
        seglen = math.hypot(dx, dy) or 1e-9
        dmax, idx = 0.0, 0
        for i in range(i0 + 1, i1):
            px, py = points[i]
            d = abs((px - ax) * dy - (py - ay) * dx) / seglen
            if d > dmax:
                dmax, idx = d, i
        if dmax > eps:
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return [p for p, k in zip(points, keep) if k]


def polyline_len(pts: list[Point]) -> float:
    return sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))


def split_long(pts: list[Point], max_len: float) -> list[list[Point]]:
    """누적 길이가 max_len을 넘을 때마다 분할."""
    if polyline_len(pts) <= max_len:
        return [pts]
    out: list[list[Point]] = []
    cur, acc = [pts[0]], 0.0
    for i in range(1, len(pts)):
        seg = math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        cur.append(pts[i])
        acc += seg
        if acc >= max_len and i < len(pts) - 1:
            out.append(cur)
            cur, acc = [pts[i]], 0.0
    if len(cur) >= 2:
        out.append(cur)
    elif out:
        out[-1].extend(cur[1:])
    return out


def trace_skeleton(binary: np.ndarray) -> list[list[Point]]:
    """skeleton → node-to-node 폴리라인 목록. 좌표는 (x, y), 분석 해상도."""
    skel = skeletonize(binary)
    pix = set(map(tuple, np.argwhere(skel)))  # (y, x)

    def nbs(p):
        y, x = p
        out = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                q = (y + dy, x + dx)
                if q in pix:
                    out.append(q)
        return out

    deg = {p: len(nbs(p)) for p in pix}
    nodes = {p for p, d in deg.items() if d != 2}
    visited: set[tuple] = set()

    def key(a, b):
        return (a, b) if a <= b else (b, a)

    paths = []
    # node → node 분기 추적
    for node in sorted(nodes):
        for nb in sorted(nbs(node)):
            if key(node, nb) in visited:
                continue
            path = [node, nb]
            visited.add(key(node, nb))
            prev, cur = node, nb
            guard = 0
            while cur not in nodes and guard < 100000:
                guard += 1
                nxt = [q for q in nbs(cur) if q != prev and key(cur, q) not in visited]
                if not nxt:
                    break
                q = nxt[0]
                visited.add(key(cur, q))
                path.append(q)
                prev, cur = cur, q
            paths.append(path)
    # 남은 loop(전부 degree 2) 추적
    for start in sorted(pix):
        for nb in sorted(nbs(start)):
            if key(start, nb) in visited:
                continue
            path = [start, nb]
            visited.add(key(start, nb))
            prev, cur = start, nb
            guard = 0
            while guard < 100000:
                guard += 1
                nxt = [q for q in nbs(cur) if q != prev and key(cur, q) not in visited]
                if not nxt:
                    break
                q = nxt[0]
                visited.add(key(cur, q))
                path.append(q)
                prev, cur = cur, q
                if cur == start:
                    break
            paths.append(path)
    # (y,x) → (x,y)
    return [[(float(x), float(y)) for (y, x) in p] for p in paths]


def content_mask(image: Image.Image, aw: int, ah: int, lum_thresh: float, sat_thresh: float,
                 close_radius: int = 0) -> tuple[np.ndarray, float]:
    """콘텐츠(비종이) 마스크와 종이 밝기 추정치를 반환."""
    img = image.convert("RGB").resize((aw, ah), Image.LANCZOS)
    arr = np.asarray(img).astype(np.float32)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    sat = np.max(arr, axis=2) - np.min(arr, axis=2)
    paper_lum = float(np.percentile(lum, 96))  # 종이 밝기 추정
    mask = ((paper_lum - lum) > lum_thresh) | (sat > sat_thresh)
    if mask.any():
        if close_radius > 0:
            mask = closing(mask, disk(close_radius))  # 잔가지 억제(가까운 획 병합)
        mask = dilation(mask, disk(1))
    return mask, paper_lum


def _raster(routes: list[_Route], aw: int, ah: int, up: float) -> np.ndarray:
    """route 목록을 분석 해상도 커버 마스크로 래스터화."""
    m = Image.new("1", (aw, ah), 0)
    d = ImageDraw.Draw(m)
    for rt in routes:
        wpx = max(1, int(round(rt.width / up)))
        pts = rt.pts
        if len(pts) >= 2:
            d.line(pts, fill=1, width=wpx, joint="curve")
        rad = wpx / 2
        for (x, y) in (pts[0], pts[-1]):
            d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=1)
    return np.asarray(m, dtype=bool)


def _seal_bands(missing: np.ndarray, step: int, seal_width: float, axis: str) -> list[_Route]:
    """미커버 픽셀을 수평(axis='h')/수직(axis='v') run 밴드로 채우는 seal route 생성."""
    ah, aw = missing.shape
    out: list[_Route] = []
    if axis == "h":
        for y in range(0, ah, step):
            row = missing[y]
            x = 0
            while x < aw:
                if row[x]:
                    x0 = x
                    while x < aw and row[x]:
                        x += 1
                    if (x - 1) - x0 >= 2:
                        out.append(_Route([(float(x0), float(y)), (float(x - 1), float(y))], seal_width, "seal"))
                else:
                    x += 1
    else:
        for x in range(0, aw, step):
            col = missing[:, x]
            y = 0
            while y < ah:
                if col[y]:
                    y0 = y
                    while y < ah and col[y]:
                        y += 1
                    if (y - 1) - y0 >= 2:
                        out.append(_Route([(float(x), float(y0)), (float(x), float(y - 1))], seal_width, "seal"))
                else:
                    y += 1
    return out


def _seal_residual_components(missing: np.ndarray, up: float) -> list[_Route]:
    """마지막 미세 잉크 성분을 round-cap route로 마감한다.

    가로·세로 seal band는 긴 잉크를 효율적으로 덮지만, 분석 해상도에서 1~2px로
    남은 anti-alias 성분은 band step 사이에 놓일 수 있다. 이 보강은 그런 성분만
    각각 감싸며 원본 이미지를 덮거나 outline 마스크를 넓히지 않는다.
    """
    from scipy import ndimage

    labels, count = ndimage.label(missing, structure=np.ones((3, 3), int))
    out: list[_Route] = []
    for label in range(1, count + 1):
        ys, xs = np.nonzero(labels == label)
        if not len(xs):
            continue
        cx = float((xs.min() + xs.max()) / 2)
        cy = float((ys.min() + ys.max()) / 2)
        radius = float(np.hypot(xs - cx, ys - cy).max())
        delta = min(0.45, max(0.15, radius * 0.08))
        # target px width → _raster가 분석 px로 되돌리므로 분석상 반경은
        # 항상 component radius보다 1px 이상 크다.
        width = max(3.0 * up, float(math.ceil(radius * 2 + 2)) * up)
        out.append(_Route([(cx - delta, cy - delta), (cx + delta, cy + delta)], width, "seal"))
    return out


def _order_routes(routes: list[_Route], aw: int, ah: int, up: float, seed: int,
                  start_pt: Point | None = None) -> list[_Route]:
    """source-inspired ordering: 이동 anchor 순회 + travel/재방문 penalty.

    start_pt 지정 시 시작 위치만 그 점으로 잇는다 (존 체이닝용 — 기본 동작 불변).
    """
    rng = np.random.default_rng(seed)
    anchors_norm = [(0.16, 0.22), (0.82, 0.2), (0.5, 0.5), (0.22, 0.8),
                    (0.8, 0.82), (0.5, 0.24), (0.3, 0.6), (0.72, 0.62), (0.16, 0.5)]
    anchors = [(ax * aw, ay * ah) for (ax, ay) in anchors_norm]
    total = len(routes)
    # 고해상도 스캔에서 seal route가 수천 개가 되면 매번 모든 후보를 비교하는
    # O(n²) greedy 정렬이 수십 분을 점유한다. 이 경우에는 동일한 3x3 공간
    # 버킷 안에서 지그재그로 정렬해 이동을 짧게 유지한다. route 자체와 coverage는
    # 바꾸지 않으며, 작은 route 집합은 기존의 세밀한 greedy 경로를 그대로 쓴다.
    if total > 1200:
        cell_w, cell_h = aw / 3, ah / 3
        def serpentine_key(rt: _Route) -> tuple[int, int, float, float]:
            row = min(2, int(rt.cy / cell_h))
            col = min(2, int(rt.cx / cell_w))
            return (row, col, rt.cy, rt.cx if row % 2 == 0 else -rt.cx)
        return sorted(routes, key=serpentine_key)
    advance_every = max(1, total // (len(anchors) * 2))

    def zone(rt: _Route) -> int:
        zx = min(2, int(rt.cx / (aw / 3)))
        zy = min(2, int(rt.cy / (ah / 3)))
        return zy * 3 + zx

    remaining = routes[:]
    ordered: list[_Route] = []
    last_pt = start_pt if start_pt is not None else anchors[0]
    ai = 0
    same_zone_run = 0
    last_zone = -1
    while remaining:
        anchor = anchors[ai % len(anchors)]
        best, best_score, best_rev = None, 1e18, False
        for rt in remaining:
            s0, s1 = rt.pts[0], rt.pts[-1]
            t_start = math.hypot(s0[0] - last_pt[0], s0[1] - last_pt[1])
            t_end = math.hypot(s1[0] - last_pt[0], s1[1] - last_pt[1])
            travel = min(t_start, t_end)
            rev = t_end < t_start
            target_d = math.hypot(rt.cx - anchor[0], rt.cy - anchor[1])
            long_jump = max(0.0, travel - 360 / up) * 0.5
            zpen = 55 if zone(rt) == last_zone and same_zone_run >= 6 else 0
            jitter = float(rng.random()) * 14
            score = target_d * 0.5 + travel * 0.34 + long_jump + zpen + jitter
            if score < best_score:
                best, best_score, best_rev = rt, score, rev
        remaining.remove(best)
        if best_rev:
            best.pts = best.pts[::-1]
        ordered.append(best)
        last_pt = best.pts[-1]
        z = zone(best)
        same_zone_run = same_zone_run + 1 if z == last_zone else 0
        last_zone = z
        if len(ordered) % advance_every == 0:
            ai += 1
    return ordered


def _compute_zones(content: np.ndarray, merge_r: int) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """잉크 마스크 팽창(근접 성분 병합) 후 연결 성분 라벨링 → (라벨 배열, 존 중심 목록)."""
    from scipy import ndimage
    dilated = ndimage.binary_dilation(content, structure=np.ones((3, 3), bool),
                                      iterations=max(1, merge_r))
    lbl, n = ndimage.label(dilated, structure=np.ones((3, 3), int))
    centers = ndimage.center_of_mass(dilated, lbl, range(1, n + 1))  # [(y, x), ...]
    return lbl, [(float(x), float(y)) for (y, x) in centers]


def _order_by_zone(routes: list[_Route], content: np.ndarray, aw: int, ah: int, up: float,
                   seed: int, merge_px: float) -> tuple[list[_Route], int, list[dict]]:
    """매크로 존 단위 순서: 존 배정 → 최좌상 존 시작 근접 순회(결정적) → 존 내부 기존 순회.

    반환: (정렬된 routes, zoneCount, 드로잉 순서 존 정보). 존 간 순간이동은 존 수 - 1 회.
    존 정보: [{zone, inkPixels(분석 px), bbox(target px [x0,y0,x1,y1]), strokeCount}]
    """
    merge_r = max(1, int(round(merge_px / up)))
    lbl, centers = _compute_zones(content, merge_r)
    if not centers:
        return _order_routes(routes, aw, ah, up, seed), 0, []

    def assign(rt: _Route) -> int:
        y, x = min(ah - 1, max(0, int(rt.cy))), min(aw - 1, max(0, int(rt.cx)))
        z = int(lbl[y, x])
        if z > 0:
            return z - 1
        d2 = [(rt.cx - cx) ** 2 + (rt.cy - cy) ** 2 for (cx, cy) in centers]
        return int(np.argmin(d2))

    groups: dict[int, list[_Route]] = {}
    for rt in routes:
        groups.setdefault(assign(rt), []).append(rt)

    # 존 순회: 최좌상(cx+cy 최소) 존에서 시작, 중심 근접 그리디 (seed 무관 결정적)
    remaining = sorted(groups.keys())
    zone_order: list[int] = []
    cur = min(remaining, key=lambda z: centers[z][0] + centers[z][1])
    while True:
        zone_order.append(cur)
        remaining.remove(cur)
        if not remaining:
            break
        cx, cy = centers[cur]
        cur = min(remaining, key=lambda z: ((centers[z][0] - cx) ** 2 + (centers[z][1] - cy) ** 2, z))

    ordered_all: list[_Route] = []
    zone_infos: list[dict] = []
    last_pt: Point | None = None
    for zi, z in enumerate(zone_order):
        ordered = _order_routes(groups[z], aw, ah, up, seed, start_pt=last_pt)
        for rt in ordered:
            rt.zone = zi
        ordered_all.extend(ordered)
        last_pt = ordered[-1].pts[-1]
        # 존 잉크 질량/바운딩박스 (분석 해상도 → bbox 는 target px 로 환산)
        zmask = (lbl == z + 1) & content
        ys, xs = np.nonzero(zmask)
        bbox = [int(xs.min() * up), int(ys.min() * up),
                int((xs.max() + 1) * up), int((ys.max() + 1) * up)] if len(xs) else [0, 0, 0, 0]
        zone_infos.append({"zone": zi, "inkPixels": int(zmask.sum()), "bbox": bbox,
                           "strokeCount": len(ordered)})
    return ordered_all, len(centers), zone_infos


def _assign_timing(ordered: list[_Route], up: float, draw_start: float, draw_end: float) -> list[dict]:
    """gap ∝ travel, draw ∝ length 단위를 [draw_start, draw_end] 프레임 구간으로 스케일."""
    gaps, draws = [], []
    prev_pt = ordered[0].pts[0]
    for rt in ordered:
        s0 = rt.pts[0]
        travel = math.hypot(s0[0] - prev_pt[0], s0[1] - prev_pt[1]) * up
        gaps.append(min(0.55, max(0.03, travel / 2100.0)))
        draws.append(max(0.22, min(1.6, (rt.length * up) / 640.0)))
        prev_pt = rt.pts[-1]
    total_units = sum(gaps) + sum(draws)
    scale = (draw_end - draw_start) / max(1e-6, total_units)
    t = draw_start
    strokes = []
    for i, rt in enumerate(ordered):
        t += gaps[i] * scale
        st = t
        t += draws[i] * scale
        pts_t = [[round(x * up, 1), round(y * up, 1)] for (x, y) in rt.pts]
        stroke = {
            "id": f"s{i:04d}", "kind": rt.kind, "width": round(rt.width, 1),
            "start": round(st, 2), "end": round(t, 2), "points": pts_t,
        }
        if rt.zone is not None:  # 존 그룹핑 경로에서만 — brush 산출물 불변 (TS parse 는 추가 필드 무시)
            stroke["zone"] = rt.zone
        strokes.append(stroke)
    return strokes


def generate_routes(image_path: str | Path, params: RouteParams | None = None,
                    image_rel: str | None = None) -> dict:
    """이미지 → routes JSON dict {meta, strokes}. 백지 입력이면 빈 strokes + 경고."""
    p = params or RouteParams()
    W, H = p.width, p.height
    aw, ah = int(round(W / p.analyze_scale)), int(round(H / p.analyze_scale))
    up = W / aw  # 분석→타깃 좌표 배율

    img = Image.open(image_path)
    content, paper_lum = content_mask(img, aw, ah, p.lum_thresh, p.sat_thresh, p.close)
    content_count = int(content.sum())

    draw_start = p.draw_start
    draw_end = p.draw_end if p.draw_end is not None else p.duration * 0.88
    pen_off = p.pen_invisible_after if p.pen_invisible_after is not None else p.duration * 0.94

    if image_rel is None:
        s = str(image_path).replace("\\", "/")
        image_rel = s.split("public/", 1)[1] if "public/" in s else s

    def build_meta(strokes, contour_n, seal_n, coverage, missing, zone_count=None):
        meta = {
            "image": image_rel,
            "width": W, "height": H, "fps": p.fps, "durationInFrames": p.duration,
            "drawStart": round(draw_start, 2), "drawEnd": round(draw_end, 2),
            "penInvisibleAfter": round(pen_off, 2),
            "routeCount": len(strokes), "contourCount": contour_n, "sealCount": seal_n,
            "coverage": round(coverage, 4), "missingPixels": missing, "contentPixels": content_count,
            "paperLum": round(paper_lum, 1), "seed": p.seed,
        }
        if zone_count is not None:  # group_by_zone 경로에서만 — False 경로 산출물 불변
            meta["zoneCount"] = zone_count
        return meta

    if content_count == 0:
        log.warning("콘텐츠 픽셀 없음(백지 이미지): %s — 빈 strokes 반환", image_path)
        return {"meta": build_meta([], 0, 0, 1.0, 0, zone_count=0 if p.group_by_zone else None),
                "strokes": []}

    # contour routes
    contour: list[_Route] = []
    for raw in trace_skeleton(content):
        if len(raw) < 2:
            continue
        simp = rdp(raw, p.rdp_eps)
        if len(simp) < 2:
            continue
        for seg in split_long(simp, p.max_len / up):
            if polyline_len(seg) * up < p.min_route_len:
                continue  # 짧은 잔가지는 seal이 커버
            contour.append(_Route(seg, p.contour_width, "contour"))

    # seal: 미커버 픽셀 밴드 채움 (수평 → 수직)
    step = max(2, int(round((p.seal_step if p.seal_step is not None else p.seal_width * 0.9) / up)))
    covered = _raster(contour, aw, ah, up) if contour else np.zeros((ah, aw), bool)
    seal = _seal_bands(content & ~covered, step, p.seal_width, "h")
    covered2 = _raster(contour + seal, aw, ah, up) if (contour or seal) else covered
    seal += _seal_bands(content & ~covered2, step, p.seal_width, "v")
    # pen-brush는 exterior recall을 strict QA하므로, zone-mode에서만 남은
    # anti-alias 잉크 섬을 정확히 마감한다. 일반 brush output은 golden route
    # 계약을 그대로 유지한다.
    if p.group_by_zone:
        covered3 = _raster(contour + seal, aw, ah, up)
        seal += _seal_residual_components(content & ~covered3, up)

    routes = contour + seal
    if not routes:
        log.warning("생성된 route 없음: %s — 빈 strokes 반환", image_path)
        return {"meta": build_meta([], 0, 0, 0.0, content_count,
                                   zone_count=0 if p.group_by_zone else None),
                "strokes": []}

    for rt in routes:
        xs = [q[0] for q in rt.pts]
        ys = [q[1] for q in rt.pts]
        rt.cx, rt.cy = sum(xs) / len(xs), sum(ys) / len(ys)
        rt.length = polyline_len(rt.pts)

    zone_infos: list[dict] = []
    if p.group_by_zone:
        ordered, zone_count, zone_infos = _order_by_zone(routes, content, aw, ah, up,
                                                         p.seed, p.zone_merge_px)
    else:
        ordered, zone_count = _order_routes(routes, aw, ah, up, p.seed), None
    strokes = _assign_timing(ordered, up, draw_start, draw_end)

    # 존 정보에 타이밍(첫/마지막 스트로크) 채움 — zones.json/동기 배분용
    for zi in zone_infos:
        zs = [s for s in strokes if s.get("zone") == zi["zone"]]
        zi["start"] = zs[0]["start"] if zs else None
        zi["end"] = zs[-1]["end"] if zs else None

    covered_final = _raster(routes, aw, ah, up)
    inside = int((content & covered_final).sum())
    missing = content_count - inside
    coverage = inside / max(1, content_count)
    result = {"meta": build_meta(strokes, len(contour), len(seal), coverage, missing, zone_count),
              "strokes": strokes}
    if p.group_by_zone:
        result["zones"] = zone_infos  # write_routes 는 meta/strokes 만 기록 (routes 포맷 불변)
    return result


def write_routes(data: dict, out_path: str | Path) -> Path:
    """routes JSON 저장 — 포맷 계약 유지를 위해 meta/strokes 만 기록."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": data["meta"], "strokes": data["strokes"]}), encoding="utf-8")
    return out


def render_preview(image_path: str | Path, data: dict, out_path: str | Path) -> Path:
    """QA용 route preview PNG — 순서를 색상으로 표시."""
    meta = data["meta"]
    prev = Image.open(image_path).convert("RGB").resize((meta["width"], meta["height"]), Image.LANCZOS)
    dd = ImageDraw.Draw(prev, "RGBA")
    n = len(data["strokes"])
    for i, s in enumerate(data["strokes"]):
        hue = int(255 * i / max(1, n))
        col = (hue, 60, 255 - hue, 130) if s["kind"] == "contour" else (0, hue, 120, 70)
        pts = [(x, y) for x, y in s["points"]]
        if len(pts) >= 2:
            dd.line(pts, fill=col, width=max(1, int(s["width"] * 0.5)))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    prev.save(out)
    return out

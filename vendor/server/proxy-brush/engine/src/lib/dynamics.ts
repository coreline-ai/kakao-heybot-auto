// 스트로크 변형(brushDynamics) — 전부 seed 기반 deterministic. Math.random 사용 금지.
// 클램프 범위와 수식은 참조 시스템의 튜닝 결과를 채택한다.
import type { BrushDynamics, Stroke } from "../schema";
import type { Point } from "./geometry";

/** deterministic 0~1 해시. 같은 (seed, key, salt)면 항상 같은 값. */
export const hash01 = (seed: number, key: string, salt = 0): number => {
  let h = (2166136261 ^ Math.round(seed * 1009) ^ (salt * 374761393)) >>> 0;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  h ^= h >>> 13;
  h = Math.imul(h, 1274126177) >>> 0;
  return (h >>> 0) / 4294967295;
};

export const normalizeBrushDynamics = (d?: Partial<BrushDynamics>) => ({
  drawSpeedScale: Math.max(0.25, Math.min(2.0, d?.drawSpeedScale ?? 1)),
  touchScale: Math.max(0.2, Math.min(3.0, d?.touchScale ?? 1)),
  touchJitter: Math.max(0, Math.min(0.8, d?.touchJitter ?? 0)),
  pathJitter: Math.max(0, Math.min(120, d?.pathJitter ?? 0)),
  randomizeOrder: Boolean(d?.randomizeOrder),
  randomReverse: Boolean(d?.randomReverse),
  seed: Math.round(d?.seed ?? 1),
});

/** path에 deterministic 흔들림을 준다. 끝점은 덜 흔들어(soften) 이음새가 어긋나지 않게 한다. */
export const jitterPoints = (points: Point[], seed: number, key: string, amount: number): Point[] => {
  if (amount <= 0 || points.length < 2) return points;
  const jitterAt = (i: number, axis: number, soften = 1) =>
    (hash01(seed, `${key}:${i}:${axis}`, 71) * 2 - 1) * amount * soften;
  if (points.length === 2) {
    // 2점 직선은 4점으로 보간해 중간을 흔든다 (seal 밴드가 기계적 직선으로 보이는 것을 완화)
    const [a, b] = points;
    return [0, 1, 2, 3].map((i) => {
      const t = i / 3;
      const endSoften = i === 0 || i === 3 ? 0.28 : 1;
      return [
        a[0] + (b[0] - a[0]) * t + jitterAt(i, 0, endSoften),
        a[1] + (b[1] - a[1]) * t + jitterAt(i, 1, endSoften),
      ];
    });
  }
  return points.map(([x, y], i) => {
    const endSoften = i === 0 || i === points.length - 1 ? 0.35 : 1;
    return [x + jitterAt(i, 0, endSoften), y + jitterAt(i, 1, endSoften)];
  });
};

/**
 * routes의 원본 스트로크에 brushDynamics를 적용한다.
 * - drawSpeedScale: 첫 시작점 기준으로 타임라인을 늘림
 * - randomizeOrder: 타임 슬롯은 유지하고 geometry만 랜덤 배치 (전체 길이 불변)
 * - touchScale/touchJitter: width 배율·랜덤 편차, randomReverse: 진행 방향 반전
 */
export const buildDynamicStrokes = (
  strokes: Stroke[],
  penInvisibleAfter: number,
  brushDynamics?: Partial<BrushDynamics>,
): { strokes: Stroke[]; penInvisibleAfter: number; drawEnd: number } => {
  const dyn = normalizeBrushDynamics(brushDynamics);
  if (!strokes.length) return { strokes, penInvisibleAfter, drawEnd: penInvisibleAfter };

  const slots = [...strokes].sort((a, b) => a.start - b.start);
  const geometries = dyn.randomizeOrder
    ? [...strokes].sort((a, b) => hash01(dyn.seed, a.id, 11) - hash01(dyn.seed, b.id, 11))
    : slots;
  const firstStart = Math.min(...slots.map((s) => s.start));

  const nextStrokes = slots.map((slot, i) => {
    const geom = geometries[i % geometries.length];
    const start = firstStart + (slot.start - firstStart) * dyn.drawSpeedScale;
    const end = firstStart + (slot.end - firstStart) * dyn.drawSpeedScale;
    const jitter = dyn.touchJitter > 0 ? 1 + (hash01(dyn.seed, geom.id, 23) * 2 - 1) * dyn.touchJitter : 1;
    const reverse = dyn.randomReverse && hash01(dyn.seed, geom.id, 37) > 0.5;
    const basePoints = reverse ? [...geom.points].reverse() : geom.points;
    return {
      ...geom,
      id: dyn.randomizeOrder ? `${geom.id}-slot-${slot.id}` : geom.id,
      start,
      end,
      width: Math.max(1, geom.width * dyn.touchScale * jitter),
      points: jitterPoints(basePoints as Point[], dyn.seed, geom.id, dyn.pathJitter),
    };
  });

  const nextPenOff = firstStart + (penInvisibleAfter - firstStart) * dyn.drawSpeedScale;
  const drawEnd = Math.max(...nextStrokes.map((s) => s.end));
  return { strokes: nextStrokes, penInvisibleAfter: nextPenOff, drawEnd };
};

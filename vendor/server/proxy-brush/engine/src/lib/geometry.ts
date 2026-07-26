// 폴리라인 좌표 계산 — 순수 함수만 둔다 (렌더 의존성 없음).
export type Point = [number, number];

export const toPath = (pts: Point[]): string =>
  pts.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");

/** 폴리라인 위 진행률 t(0~1) 지점의 좌표와 진행 방향 각도(deg). */
export function pointOnPolyline(points: Point[], t: number): { x: number; y: number; angle: number } {
  if (points.length < 2) return { x: points[0]?.[0] ?? 0, y: points[0]?.[1] ?? 0, angle: 0 };
  const lens: number[] = [];
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const len = Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
    lens.push(len);
    total += len;
  }
  const target = Math.max(0, Math.min(1, t)) * total;
  let acc = 0;
  for (let i = 1; i < points.length; i++) {
    const len = lens[i - 1];
    if (acc + len >= target || i === points.length - 1) {
      const local = len === 0 ? 0 : (target - acc) / len;
      const a = points[i - 1];
      const b = points[i];
      return {
        x: a[0] + (b[0] - a[0]) * local,
        y: a[1] + (b[1] - a[1]) * local,
        angle: (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI,
      };
    }
    acc += len;
  }
  const last = points[points.length - 1];
  const prev = points[points.length - 2];
  return { x: last[0], y: last[1], angle: (Math.atan2(last[1] - prev[1], last[0] - prev[0]) * 180) / Math.PI };
}

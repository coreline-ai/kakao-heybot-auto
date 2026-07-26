// 진행률 계산 — 이징 곡선과 스냅 규칙은 참조 시스템의 튜닝 결과를 채택한다.
import { Easing, interpolate } from "remotion";

export const clamp = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const };
export const easeDraw = { ...clamp, easing: Easing.out(Easing.cubic) };
export const easeTravel = { ...clamp, easing: Easing.inOut(Easing.cubic) };
export const easeDevelop = { ...clamp, easing: Easing.inOut(Easing.cubic) };

/**
 * 스트로크 진행률 (0→1).
 * linear=false: easeDraw 이징 + raw 72% 지점에서 조기 완성 스냅 (붓이 끝을 오래 끌지 않게).
 * linear=true: 이징/스냅 없이 등속 — 단일 연속 스트로크 드로잉용.
 */
export function sharedProgress(frame: number, start: number, end: number, linear = false): number {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  if (end <= start) return frame < start ? 0 : 1;
  const raw = interpolate(frame, [start, end], [0, 1], clamp);
  if (linear) return raw;
  const eased = interpolate(frame, [start, end], [0, 1], easeDraw);
  return raw >= 0.72 ? 1 : eased;
}

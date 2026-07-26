// 전환·플래시 순수 함수 — 원본 KIMI-K3 전환 4구간 프레임 분석(2026-07-19)의 계측 스펙.
// white-flash: 전프레임 노출 리프트 베일, peak ~0.4, 상승 2f·감쇠 4f. 컷과 씬 내부 비트 양쪽에 사용.
import { interpolate } from "remotion";
import type { PromoScene } from "../schema";

export type SceneWindow = { start: number; end: number };

// 씬 시작/끝 프레임 (테스트 대상)
export function sceneLayout(scenes: Pick<PromoScene, "durationInFrames">[]): SceneWindow[] {
  const out: SceneWindow[] = [];
  let acc = 0;
  for (const s of scenes) {
    out.push({ start: acc, end: acc + s.durationInFrames });
    acc += s.durationInFrames;
  }
  return out;
}

export function totalDuration(scenes: Pick<PromoScene, "durationInFrames">[]): number {
  return scenes.reduce((sum, s) => sum + s.durationInFrames, 0);
}

// 현재 프레임의 씬 인덱스 (마지막 프레임 클램프)
export function activeSceneIndex(layout: SceneWindow[], frame: number): number {
  for (let i = 0; i < layout.length; i++) {
    if (frame < layout[i].end) return i;
  }
  return layout.length - 1;
}

// 전환 진행률 0→1 — 씬 진입 후 durationInFrames 동안 (전환 없으면 항상 1)
export function transitionProgress(sceneStart: number, frame: number, durationInFrames: number): number {
  if (durationInFrames <= 0) return 1;
  return interpolate(frame, [sceneStart, sceneStart + durationInFrames], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

// 플래시 베일 불투명도 — beatFrame 기준 상승 1f → peak 0.5 → 감쇠 4f.
// 재보정(2026-07-19): 원본 스파이크 프레임간 diff ~37% 역산 (v2의 peak 0.4·상승 2f는 diff ~16%로 원본 대비 절반).
export const FLASH_PEAK = 0.5;
export function flashOpacity(localFrame: number, beatFrame: number): number {
  return interpolate(
    localFrame,
    [beatFrame - 1, beatFrame, beatFrame + 4],
    [0, FLASH_PEAK, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
}

// 씬의 모든 플래시(진입 white-flash + flashAt 비트) 합성 불투명도
export function sceneFlash(scene: Pick<PromoScene, "transition" | "flashAt">, localFrame: number): number {
  let v = 0;
  if (scene.transition.type === "white-flash") v = Math.max(v, flashOpacity(localFrame, 1));
  for (const beat of scene.flashAt) v = Math.max(v, flashOpacity(localFrame, beat));
  return v;
}

// light-sweep 빔 중심 x 위치(0→1) — 화면 밖(-0.08)에서 밖(1.08)까지 통과
export function sweepX(progress: number): number {
  return -0.08 + progress * 1.16;
}

// 씬 카메라 transform (P7) — 씬 길이 동안 선형 진행. 프레임 전체가 숨쉬는 원본 감각 (테스트 대상 순수 함수).
export function cameraTransform(
  camera: { move: "none" | "push-in" | "pull-back" | "drift-left" | "drift-right"; amount: number },
  localFrame: number,
  durationInFrames: number,
): string {
  if (camera.move === "none" || camera.amount <= 0) return "none";
  const p = Math.max(0, Math.min(1, durationInFrames > 0 ? localFrame / durationInFrames : 1));
  switch (camera.move) {
    case "push-in":
      return `scale(${(1 + camera.amount * p).toFixed(4)})`;
    case "pull-back":
      return `scale(${(1 + camera.amount * (1 - p)).toFixed(4)})`;
    case "drift-left":
      return `scale(${(1 + camera.amount).toFixed(4)}) translateX(${(camera.amount * 480 * p).toFixed(1)}px)`;
    case "drift-right":
      return `scale(${(1 + camera.amount).toFixed(4)}) translateX(${(-camera.amount * 480 * p).toFixed(1)}px)`;
  }
}

// 프로모 위젯 공용 셸 — 절대 배치 + 등장 모션(rise/fade) + 상단 label 슬롯.
// 다크 프로모 언어는 카드 크롬이 없다(투명 컨테이너). 카드가 필요한 위젯(리더보드)은 바디가 자체 패널을 그린다.
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "./schema";
import { P, SETTLE_FRAMES, labelStyle, promoEase } from "./tokens";

// brushIn 리빌 지속 프레임 — 붓이 위젯을 세 번 쓸어내리는 시간
export const BRUSH_IN_FRAMES = 26;

// 붓질 마스크 clip-path (P6-B) — 3패스 L→R 스태거 스윕 + 결정적 거친 모서리(hash01).
// progress 1 이상이면 undefined(클립 해제). 테스트 대상 순수 함수.
export function brushClipPath(progress: number, seed = 0): string | undefined {
  if (progress >= 1) return undefined;
  const bands = 3;
  const edgePoints = 5;
  const pts: string[] = [];
  const edgeX = (band: number): number => {
    const p = Math.max(0, Math.min(1, progress * 1.6 - band * 0.3)); // 밴드별 스태거
    return Math.max(0, Math.min(100, -8 + p * 116));
  };
  pts.push("0% 0%");
  for (let b = 0; b < bands; b++) {
    const yTop = (b / bands) * 100;
    const yBot = ((b + 1) / bands) * 100;
    const x = edgeX(b);
    // 밴드 우측 거친 모서리 — 위→아래
    for (let k = 0; k <= edgePoints; k++) {
      const y = yTop + ((yBot - yTop) * k) / edgePoints;
      const jitter = (hash01(seed * 31 + b * 7 + k) - 0.5) * 7;
      const xj = Math.max(0, Math.min(100, x + jitter));
      pts.push(`${xj.toFixed(2)}% ${y.toFixed(2)}%`);
    }
  }
  pts.push("0% 100%");
  return `polygon(${pts.join(", ")})`;
}

export const PromoShell: React.FC<{ widget: PromoWidget; frame: number; children: React.ReactNode }> = ({
  widget: w,
  frame,
  children,
}) => {
  const brush = w.entrance === "brushIn";
  const appear = brush
    ? interpolate(frame, [w.enterAt, w.enterAt + 3], [0, 1], promoEase) // 마스크가 리빌 담당 — 초입 3f 페이드만
    : interpolate(frame, [w.enterAt, w.enterAt + SETTLE_FRAMES], [0, 1], promoEase);
  const rise = brush ? 0 : interpolate(frame, [w.enterAt, w.enterAt + SETTLE_FRAMES + 4], [12, 0], promoEase); // 붓질은 제자리에서 그려진다
  if (appear <= 0.001) return null;
  const brushProgress = promoProgress(frame, w.enterAt, BRUSH_IN_FRAMES);
  const clip = brush ? brushClipPath(brushProgress, Math.round(w.x + w.y)) : undefined;
  // 위젯은 정착 후 완전 고정, 등장도 스케일 없이 fade+rise만 (2026-07-19 사용자 피드백:
  // 개별 드리프트·확대는 떨림/조잡함으로 읽힘. 움직임은 값 애니메이션·마퀴·무대의 몫)
  return (
    <div
      style={{
        position: "absolute",
        left: w.x,
        top: w.y,
        width: w.w,
        height: w.h,
        boxSizing: "border-box",
        color: P.text,
        opacity: appear,
        transform: rise !== 0 ? `translateY(${rise}px)` : undefined,
        clipPath: clip,
      }}
    >
      {w.label && <div style={{ ...labelStyle(12), marginBottom: 10 }}>{w.label}</div>}
      <div style={{ position: "absolute", left: 0, right: 0, top: w.label ? 28 : 0, bottom: 0 }}>{children}</div>
    </div>
  );
};

// 진행률 헬퍼 — 위젯 값 애니메이션의 단일 근원 (테스트 대상 순수 함수)
export function promoProgress(frame: number, enterAt: number, durationFrames: number): number {
  if (durationFrames <= 0) return frame < enterAt ? 0 : 1;
  return interpolate(frame, [enterAt, enterAt + durationFrames], [0, 1], promoEase);
}

// min/max 정규화 + 클램프 (경계값 방어의 단일 근원)
export function normalizeRatio(value: number, min: number, max: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(min) || !Number.isFinite(max) || max <= min) return 0;
  return Math.max(0, Math.min(1, (value - min) / (max - min)));
}

// 결정적 의사난수 (파티클/플립 등) — Math.random 금지 계약의 대체. 같은 i는 항상 같은 값.
export function hash01(i: number): number {
  const s = Math.sin((i + 1) * 127.1) * 43758.5453;
  return s - Math.floor(s);
}

// 데이터 패널 공통 외곽 (노드 그래프·오실로스코프·지층 등)
export const panelStyle: React.CSSProperties = {
  position: "relative",
  height: "100%",
  boxSizing: "border-box",
  background: P.panel,
  border: `${P.hairline}px solid ${P.line}`,
  borderRadius: P.radiusMd,
  overflow: "hidden",
};

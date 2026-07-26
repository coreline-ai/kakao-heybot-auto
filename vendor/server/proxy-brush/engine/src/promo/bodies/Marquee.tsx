// W26 마퀴 티커 — 풀폭 반복 텍스트 스크롤 (프레임 결정적, CSS 애니메이션 금지).
// 원본 근거: KIMI-K3 분석 씬 18·29 (MATCH HIGHLIGHTS ▸ … / UNTIL AUG 12 ▸ API TOP-UP).
import React from "react";
import type { PromoWidget } from "../schema";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "marquee" }>;

// 현재 스크롤 오프셋 px (테스트 대상 순수 함수)
export function marqueeOffset(w: W, frame: number): number {
  return Math.max(0, frame - w.enterAt) * w.speed;
}

export const MarqueeBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const offset = marqueeOffset(w, frame);
  // 페이지 지속(150f) × speed + 위젯 폭을 덮도록 충분히 반복 — 이음새 없이 등속 이동
  const strip = Array.from({ length: 48 }, () => w.text).join(w.separator);
  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        alignItems: "center",
        overflow: "hidden",
        borderTop: `${P.hairline}px solid ${P.line}`,
        borderBottom: `${P.hairline}px solid ${P.line}`,
        background: P.panel,
      }}
    >
      <div style={{ ...labelStyle(11), transform: `translateX(${-offset}px)`, whiteSpace: "nowrap" }}>{strip}</div>
    </div>
  );
};

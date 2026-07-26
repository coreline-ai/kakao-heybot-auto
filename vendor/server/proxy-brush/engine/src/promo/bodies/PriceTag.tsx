// W10 가격표 — 끈에 매달린 태그의 감쇠 펜듈럼 스윙 + 고스트 잔상 가격 드롭.
// 원본 근거: KIMI-K3 분석 씬 24-25 ($3.00 행잉 태그 → 캐시 히트 시 $0.30 드롭).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "priceTag" }>;

// 감쇠 진자 각도(deg) — 결정적 (테스트 대상 순수 함수)
export function pendulumAngle(w: W, frame: number): number {
  if (!w.swing) return 0;
  const t = Math.max(0, frame - w.enterAt);
  return 10 * Math.cos(t * 0.16) * Math.exp(-t * 0.02);
}

export const PriceTagBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const angle = pendulumAngle(w, frame);
  const appear = promoProgress(frame, w.enterAt, 12);
  const stringH = Math.max(30, w.h * 0.22);
  return (
    <div style={{ position: "relative", height: "100%", display: "grid", justifyItems: "center" }}>
      <div
        style={{
          transformOrigin: "top center",
          transform: `rotate(${angle}deg)`,
          display: "grid",
          justifyItems: "center",
          opacity: appear,
        }}
      >
        {/* 끈 */}
        <div style={{ width: P.hairline, height: stringH, background: P.lineStrong }} />
        {/* 태그 카드 */}
        <div
          style={{
            minWidth: w.w * 0.52,
            padding: "16px 22px",
            boxSizing: "border-box",
            borderRadius: P.radiusMd,
            background: P.panelStrong,
            border: `${P.hairline}px solid ${P.lineStrong}`,
            boxShadow: `0 12px 30px rgba(0,0,0,0.45)`,
            display: "grid",
            justifyItems: "center",
            gap: 6,
          }}
        >
          {w.ghostValue && (
            <div style={{ ...valueStyle(Math.max(14, w.h * 0.08), P.faint), textDecoration: "line-through" }}>{w.ghostValue}</div>
          )}
          <div style={{ ...valueStyle(Math.max(24, w.h * 0.16)), fontFamily: P.fontHero, fontWeight: 900 }}>{w.value}</div>
          {w.sub && <div style={labelStyle(9)}>{w.sub}</div>}
        </div>
      </div>
    </div>
  );
};

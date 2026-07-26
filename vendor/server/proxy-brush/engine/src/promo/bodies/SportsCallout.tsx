// W25 스포츠 콜아웃 — WINNER 골드 스탬프 / ×2 콤보 / 골드 배너 / UPGRADED 스탬프. 슬램-인 오버슈트.
// 원본 근거: KIMI-K3 분석 씬 8·11·12·24·29.
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "../schema";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "sportsCallout" }>;

// 슬램-인 스케일 (오버슈트 → 정착) — 강조 배지 전용 예외 모션 (테스트 대상 순수 함수)
export function slamScale(w: W, frame: number): number {
  return interpolate(frame, [w.enterAt, w.enterAt + 6, w.enterAt + 12], [1.7, 0.94, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

export const SportsCalloutBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const scale = slamScale(w, frame);
  const visible = frame >= w.enterAt;
  if (!visible) return null;

  if (w.variant === "banner") {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100%" }}>
        <div
          style={{
            transform: `skewX(-10deg) scale(${scale})`,
            background: P.cta,
            padding: "12px 34px",
            fontFamily: P.fontHero,
            fontStyle: "italic",
            fontWeight: 900,
            fontSize: Math.max(20, w.h * 0.32),
            letterSpacing: "0.08em",
            color: P.bg,
          }}
        >
          {w.text}
        </div>
      </div>
    );
  }

  if (w.variant === "multiKill") {
    return (
      <div style={{ display: "grid", justifyItems: "start", alignContent: "center", gap: 4, height: "100%", transform: `scale(${scale})`, transformOrigin: "left center" }}>
        {w.sub && <div style={labelStyle(11)}>{w.sub}</div>}
        <div style={{ fontFamily: P.fontHero, fontStyle: "italic", fontWeight: 900, fontSize: Math.max(30, w.h * 0.42), color: P.data }}>
          {w.text}
        </div>
        <div style={{ width: 44, height: 3, background: P.data }} />
      </div>
    );
  }

  // winner / stamp — 회전 스탬프
  const isWinner = w.variant === "winner";
  return (
    <div style={{ display: "grid", placeItems: "center", height: "100%" }}>
      <div
        style={{
          transform: `rotate(-7deg) scale(${scale})`,
          padding: "8px 20px",
          borderRadius: P.radiusSm,
          background: isWinner ? P.cta : "transparent",
          border: isWinner ? "none" : `2px dashed ${P.cta}`,
          fontFamily: P.fontHero,
          fontWeight: 900,
          fontSize: Math.max(16, w.h * 0.24),
          letterSpacing: "0.14em",
          color: isWinner ? P.bg : P.cta,
          boxShadow: isWinner ? `0 6px 18px ${P.ctaSoft}` : undefined,
        }}
      >
        {w.text}
      </div>
    </div>
  );
};

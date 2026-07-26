// W36 로고 락업 — 아이콘 배지 + 워드마크 + 언더라인 + 옵션 가랜드. dark/white 테마.
// 원본 근거: KIMI-K3 분석 씬 30 (다크 락업 → 화이트 축하 아웃트로 + 가랜드).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "logoLockup" }>;

const BUNT_COLORS = [P.data, P.cta, P.buntAlt];

const Bunting: React.FC<{ side: "left" | "right" }> = ({ side }) => (
  <div style={{ position: "absolute", top: 12, [side]: 16, display: "flex", gap: 5, transform: `rotate(${side === "left" ? 8 : -8}deg)` } as React.CSSProperties}>
    {Array.from({ length: 7 }, (_, i) => (
      <span key={i} style={{ width: 13, height: 17, clipPath: "polygon(0 0, 100% 0, 50% 100%)", background: BUNT_COLORS[i % BUNT_COLORS.length] }} />
    ))}
  </div>
);

export const LogoLockupBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const white = w.theme === "white";
  const ink = white ? "#1a2230" : P.text;
  const appear = promoProgress(frame, w.enterAt, 14);
  const heroSize = Math.max(36, Math.min(w.h * 0.32, w.w * 0.14));
  return (
    <div
      style={{
        position: "relative",
        height: "100%",
        display: "grid",
        justifyItems: "center",
        alignContent: "center",
        gap: 14,
        borderRadius: P.radiusMd,
        background: white ? "#f2f5fb" : "transparent",
        opacity: appear,
      }}
    >
      {w.bunting && (
        <>
          <Bunting side="left" />
          <Bunting side="right" />
        </>
      )}
      {w.icon && (
        <span
          style={{
            width: heroSize * 0.72,
            height: heroSize * 0.72,
            borderRadius: heroSize * 0.18,
            display: "grid",
            placeItems: "center",
            background: white ? "#11161f" : P.panelStrong,
            border: white ? "none" : `${P.hairline}px solid ${P.lineStrong}`,
            color: "#ffffff",
            fontFamily: P.fontHero,
            fontWeight: 900,
            fontSize: heroSize * 0.4,
          }}
        >
          {w.icon}
        </span>
      )}
      <span style={{ fontFamily: P.fontHero, fontWeight: 900, fontSize: heroSize, letterSpacing: "0.02em", color: ink }}>{w.wordmark}</span>
      <span style={{ width: heroSize * 1.6, height: 2.5, background: P.data }} />
      {w.sub && <span style={{ ...labelStyle(10), color: white ? "#5b6675" : P.muted }}>{w.sub}</span>}
    </div>
  );
};

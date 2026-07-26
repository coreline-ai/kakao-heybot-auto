// W-KT 키네틱 히어로 타이포 — 거대 등장 → 수축 정착 + 글로우. 원본 KIMI K3/SUPER HEAVYWEIGHT 타이틀 문법.
// 사용 관례: flashAt에 enterAt+settleFrames 비트를 두면 원본의 "정착 직후 플래시" 리듬이 된다.
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle, promoEase } from "../tokens";

type W = Extract<PromoWidget, { type: "heroTitle" }>;

// 현재 스케일 — scaleFrom → 1 수축 (테스트 대상 순수 함수)
export function heroScale(w: W, frame: number): number {
  return interpolate(frame, [w.enterAt, w.enterAt + w.settleFrames], [w.scaleFrom, 1], promoEase);
}

export const HeroTitleBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const scale = heroScale(w, frame);
  const appear = promoProgress(frame, w.enterAt, 2); // 원본: 1–2f 내 완전 등장
  const lines = w.accent ? 2 : 1;
  const size = Math.min((w.h * (lines === 2 ? 0.34 : 0.5)) / 1, w.w * 0.15);
  const items: React.CSSProperties["alignItems"] = w.align === "left" ? "flex-start" : "center";
  // 크리스프 원칙 (2026-07-19 원본 재분석): 기본 서체에 글로우 금지 — 원본의 상시 타입은 완전 플랫.
  const glow = "none";
  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: items,
        gap: size * 0.08,
        opacity: appear,
        transform: `scale(${scale})`,
        transformOrigin: w.align === "left" ? "left center" : "center center",
      }}
    >
      <div style={{ fontFamily: P.fontHero, fontWeight: 900, fontSize: size, lineHeight: 1.02, letterSpacing: "0.01em", color: P.text, textShadow: glow, whiteSpace: "nowrap" }}>
        {w.text}
      </div>
      {w.accent && (
        <div style={{ fontFamily: P.fontHero, fontWeight: 900, fontSize: size, lineHeight: 1.02, letterSpacing: "0.01em", color: P.data, textShadow: glow, whiteSpace: "nowrap" }}>
          {w.accent}
        </div>
      )}
      {w.underline && <div style={{ width: w.align === "left" ? "72%" : "58%", height: 2, background: P.data, marginTop: size * 0.12 }} />}
      {w.sub && <div style={{ ...labelStyle(Math.max(11, size * 0.1)), marginTop: size * 0.1 }}>{w.sub}</div>}
    </div>
  );
};

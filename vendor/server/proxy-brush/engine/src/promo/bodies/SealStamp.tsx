// ORIG-SEAL 낙관(落款) 스탬프 — 주사 인주 사각 도장 슬램. WINNER 스탬프의 우리식 대체 (P6-B 오리지널).
// jumun(朱文): 주사 필 사각 + 바탕색 인문 / baekmun(白文): 주사 테두리 + 주사 인문.
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "../schema";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "sealStamp" }>;

// 도장 슬램 스케일 — 높이 들었다 내리찍고 살짝 눌림 (테스트 대상 순수 함수)
export function sealSlam(w: W, frame: number): number {
  return interpolate(frame, [w.enterAt, w.enterAt + w.slamFrames * 0.6, w.enterAt + w.slamFrames], [2.2, 0.92, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
}

export const SealStampBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  if (frame < w.enterAt) return null;
  const scale = sealSlam(w, frame);
  const settled = frame >= w.enterAt + w.slamFrames;
  const jumun = w.style === "jumun";
  const size = Math.min(w.w, w.h * (w.sub ? 0.78 : 0.94));
  const chars = w.text.split("");
  const grid = chars.length > 2; // 3자 이상은 2열 세로쓰기 느낌으로
  return (
    <div style={{ height: "100%", display: "grid", justifyItems: "center", alignContent: "center", gap: 10 }}>
      <div
        style={{
          width: size,
          height: size,
          boxSizing: "border-box",
          display: "grid",
          placeItems: "center",
          padding: size * 0.1,
          borderRadius: size * 0.08,
          background: jumun ? P.cta : "transparent",
          border: jumun ? "none" : `${Math.max(3, size * 0.045)}px solid ${P.cta}`,
          boxShadow: settled ? `0 0 ${size * 0.22}px ${P.ctaSoft}` : undefined,
          transform: `rotate(-6deg) scale(${scale})`,
          opacity: interpolate(frame, [w.enterAt, w.enterAt + 2], [0.4, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
        }}
      >
        <div
          style={{
            display: "grid",
            gridTemplateColumns: grid ? "1fr 1fr" : "1fr",
            gap: size * 0.02,
            direction: "rtl", // 전통 인장 — 우→좌 배열
            fontFamily: P.fontHero,
            fontWeight: 900,
            fontSize: grid ? size * 0.32 : size * (chars.length === 1 ? 0.52 : 0.34),
            lineHeight: 1,
            color: jumun ? P.bg : P.cta,
            textAlign: "center",
          }}
        >
          {chars.map((c, i) => (
            <span key={i}>{c}</span>
          ))}
        </div>
      </div>
      {w.sub && <div style={{ ...labelStyle(10), opacity: settled ? 1 : 0 }}>{w.sub}</div>}
    </div>
  );
};

// W6 수직 랭크 래더 — 지그재그 순위 눈금 + 등반하는 pill 토큰 (#18위→단숨에 #2).
// 원본 근거: KIMI-K3 분석 씬 12b (FRONTEND ARENA 래더 climb).
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, promoEase, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "rankLadder" }>;

// 토큰의 현재 수직 위치 비율 0(최하단)→1(최상단) (테스트 대상 순수 함수)
export function ladderClimb(w: W, frame: number): number {
  return promoProgress(frame, w.enterAt, w.climbFrames);
}

export const RankLadderBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const climb = ladderClimb(w, frame);
  const n = w.ranks.length;
  const slotH = 1 / (n + 1); // 최상단 한 칸은 climbTo 도착 슬롯
  const tokenTop = interpolate(climb, [0, 1], [1 - slotH, 0.04], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: promoEase.easing });
  return (
    <div style={{ position: "relative", height: "100%" }}>
      {/* 중앙 수직 라인 */}
      <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: P.hairline, background: P.line }} />
      {/* 지그재그 순위 눈금 — 아래→위 */}
      {w.ranks.map((rank, i) => {
        const top = (1 - (i + 1) * slotH) * 100;
        const left = i % 2 === 0 ? "58%" : "12%";
        return (
          <div key={rank} style={{ position: "absolute", top: `${top}%`, left, display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 14, height: P.hairline, background: P.lineStrong }} />
            <span style={{ ...valueStyle(15, P.faint) }}>#{rank}</span>
          </div>
        );
      })}
      {/* 등반 토큰 */}
      <div
        style={{
          position: "absolute",
          top: `${tokenTop * 100}%`,
          left: "50%",
          transform: "translate(-50%, -50%)",
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "6px 14px",
          borderRadius: 999,
          background: P.data,
          boxShadow: `0 0 20px ${P.glow}`,
          ...valueStyle(16, P.text),
          whiteSpace: "nowrap",
        }}
      >
        ↗ #{w.climbTo}
      </div>
    </div>
  );
};

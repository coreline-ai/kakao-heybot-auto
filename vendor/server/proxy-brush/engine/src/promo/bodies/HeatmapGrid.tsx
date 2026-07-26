// W13 히트맵 그리드 — 셀 순차 점등 + 우측 히어로 값.
// 원본 근거: KIMI-K3 분석 씬 11 (BROWSECOMP 그리드 + 89.3).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "heatmapGrid" }>;

// 현재 점등 셀 수 (테스트 대상 순수 함수)
export function litCount(w: W, frame: number): number {
  const total = w.cols * w.rows;
  return Math.min(total, Math.floor(Math.min(w.lit, total) * promoProgress(frame, w.enterAt, w.litFrames)));
}

export const HeatmapGridBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const lit = litCount(w, frame);
  const cells = Array.from({ length: w.cols * w.rows }, (_, i) => i < lit);
  return (
    <div style={{ display: "grid", gridTemplateColumns: w.value ? "auto 1fr" : "auto", alignItems: "center", gap: 24, height: "100%" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `repeat(${w.cols}, 1fr)`,
          gap: 4,
          aspectRatio: `${w.cols} / ${w.rows}`,
          height: "100%",
          maxHeight: "100%",
          padding: 10,
          boxSizing: "border-box",
          background: P.panel,
          border: `${P.hairline}px solid ${P.line}`,
          borderRadius: P.radiusMd,
        }}
      >
        {cells.map((on, i) => (
          <span
            key={i}
            style={{
              borderRadius: 2,
              background: on ? P.data : P.track,
              boxShadow: on ? `0 0 6px ${P.glow}` : undefined,
            }}
          />
        ))}
      </div>
      {w.value && <div style={{ ...valueStyle(Math.max(28, w.h * 0.3), P.data), fontFamily: P.fontHero, fontWeight: 900 }}>{w.value}</div>}
    </div>
  );
};

// W24 pill/태그 배지 — tone(neutral·data·cta)별 보더 pill.
// 원본 근거: KIMI-K3 분석 (OPEN-SOURCE DIVISION · TERMINAL BENCH 2.1 · TICKET BOOTH).
import React from "react";
import type { PromoWidget } from "../schema";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "pillBadge" }>;

const tones = {
  neutral: { color: P.muted, border: P.line, bg: P.panelStrong },
  data: { color: P.text, border: P.data, bg: P.data },
  cta: { color: P.bg, border: P.cta, bg: P.cta },
} as const;

export const PillBadgeBody: React.FC<{ widget: W; frame: number }> = ({ widget: w }) => {
  const t = tones[w.tone];
  return (
    <div style={{ display: "flex", alignItems: "center", height: "100%" }}>
      <span
        style={{
          ...labelStyle(11),
          color: t.color,
          background: t.bg,
          border: `${P.hairline}px solid ${t.border}`,
          borderRadius: P.radiusSm,
          padding: "6px 14px",
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        {w.glyph && <span>{w.glyph}</span>}
        {w.text}
      </span>
    </div>
  );
};

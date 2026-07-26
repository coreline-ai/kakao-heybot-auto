// W32 셰브론/페넌트 배지 — 3분할 SOTA 배지, 하이라이트 골드 트로피 톤. 상단 드롭-인 stagger.
// 원본 근거: KIMI-K3 분석 씬 12 (CODING / AGENTS / VISUAL — OPEN-SOURCE SOTA).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "chevronBadges" }>;

export const ChevronBadgesBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => (
  <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "center", gap: 18, height: "100%" }}>
    {w.items.map((item, i) => {
      const appear = promoProgress(frame, w.enterAt + i * w.staggerFrames, 12);
      const width = Math.min(170, w.w / w.items.length - 18);
      return (
        <div
          key={item.label}
          style={{
            width,
            height: "82%",
            clipPath: "polygon(0 0, 100% 0, 100% 76%, 50% 100%, 0 76%)",
            background: item.highlight ? P.data : P.panelStrong,
            border: `${P.hairline}px solid ${item.highlight ? P.data : P.line}`,
            boxShadow: item.highlight ? `0 0 22px ${P.glow}` : undefined,
            display: "grid",
            justifyItems: "center",
            alignContent: "center",
            gap: 8,
            opacity: appear,
            transform: `translateY(${(1 - appear) * -18}px)`,
          }}
        >
          <span style={{ fontSize: 22, color: item.highlight ? P.cta : P.faint }}>{item.highlight ? "♛" : "◆"}</span>
          <span style={{ ...labelStyle(12), color: item.highlight ? P.text : P.muted }}>{item.label}</span>
          {item.sub && <span style={{ ...labelStyle(8), color: item.highlight ? P.cta : P.faint }}>{item.sub}</span>}
        </div>
      );
    })}
  </div>
);

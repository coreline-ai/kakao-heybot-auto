// W20 체크리스트/상태 패널 — 행별 populate + status 플래그 + 푸터.
// 원본 근거: KIMI-K3 분석 씬 7 (CODING/AGENTS/VISUAL/… READY — SCORING SYSTEM ONLINE).
import React from "react";
import type { PromoWidget } from "../schema";
import { panelStyle, promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "checklistPanel" }>;

export const ChecklistPanelBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => (
  <div style={{ display: "grid", gridTemplateRows: w.footer ? "1fr auto" : "1fr", gap: 10, height: "100%" }}>
    <div style={{ ...panelStyle, display: "grid", alignContent: "start", gap: 0, padding: "8px 0" }}>
      {w.items.map((item, i) => {
        const appear = promoProgress(frame, w.enterAt + i * w.populateFrames, 8);
        return (
          <div
            key={item.name}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "10px 16px",
              borderBottom: i < w.items.length - 1 ? `${P.hairline}px solid ${P.line}` : undefined,
              opacity: appear,
              transform: `translateX(${(1 - appear) * -8}px)`,
            }}
          >
            <span style={{ ...labelStyle(11), color: P.text }}>
              <span style={{ color: P.data, marginRight: 8 }}>◈</span>
              {item.name}
            </span>
            <span style={{ ...labelStyle(9), color: P.data }}>{item.status}</span>
          </div>
        );
      })}
    </div>
    {w.footer && (
      <div style={{ ...labelStyle(10), justifySelf: "center", opacity: promoProgress(frame, w.enterAt + w.items.length * w.populateFrames, 10) }}>
        {w.footer}
      </div>
    )}
  </div>
);

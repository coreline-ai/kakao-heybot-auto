// W34 티켓 소품 — STANDARD/VIP 티켓 카드 슬라이드-인 + 하이라이트 + UPGRADED 스탬프 슬램.
// 원본 근거: KIMI-K3 분석 씬 24 (STANDARD → VIP EXPERIENCE · UPGRADED).
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "ticketProp" }>;

export const TicketPropBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const stampAt = w.enterAt + w.tickets.length * w.slideFrames + 6;
  const stampScale = interpolate(frame, [stampAt, stampAt + 6, stampAt + 12], [1.7, 0.94, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 22, height: "100%" }}>
      {w.tickets.map((t, i) => {
        const appear = promoProgress(frame, w.enterAt + i * w.slideFrames, 12);
        return (
          <div
            key={t.tier}
            style={{
              position: "relative",
              width: t.highlight ? "44%" : "34%",
              height: t.highlight ? "84%" : "68%",
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              alignItems: "center",
              gap: 12,
              padding: "0 16px",
              boxSizing: "border-box",
              borderRadius: P.radiusMd,
              background: P.panelStrong,
              border: t.highlight ? `1.6px solid ${P.data}` : `${P.hairline}px solid ${P.line}`,
              boxShadow: t.highlight ? `0 0 24px ${P.glow}` : undefined,
              opacity: appear,
              transform: `translateX(${(1 - appear) * 24}px)`,
            }}
          >
            {/* 바코드 스트라이프 */}
            <span
              style={{
                width: 22,
                height: "62%",
                backgroundImage: `repeating-linear-gradient(90deg, ${P.lineStrong} 0 2px, transparent 2px 5px)`,
              }}
            />
            <span style={{ display: "grid", gap: 5, minWidth: 0 }}>
              <span style={{ fontFamily: P.fontHero, fontStyle: t.highlight ? "italic" : "normal", fontWeight: 900, fontSize: t.highlight ? 24 : 17, color: t.highlight ? P.text : P.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {t.tier}
              </span>
              {t.classLabel && <span style={labelStyle(8)}>{t.classLabel}</span>}
            </span>
            {/* 스탬프 — 하이라이트 티켓 상단 */}
            {t.highlight && w.stamp && frame >= stampAt && (
              <span
                style={{
                  ...labelStyle(10),
                  color: P.cta,
                  position: "absolute",
                  top: -14,
                  right: 14,
                  padding: "4px 10px",
                  border: `1.6px dashed ${P.cta}`,
                  borderRadius: P.radiusSm,
                  background: P.bg,
                  transform: `rotate(-8deg) scale(${stampScale})`,
                }}
              >
                {w.stamp}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
};

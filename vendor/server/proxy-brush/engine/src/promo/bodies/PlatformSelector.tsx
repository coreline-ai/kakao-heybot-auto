// W22 플랫폼 셀렉터 — 대형 선택 카드(코너 브래킷) + 옵션 스택 + 커넥터 + 푸터.
// 원본 근거: KIMI-K3 분석 씬 27b (WEB 선택 + APP/API — AVAILABLE NOW).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "platformSelector" }>;

export const PlatformSelectorBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const selected = promoProgress(frame, w.enterAt + w.options.length * w.staggerFrames, 12);
  return (
    <div style={{ display: "grid", gridTemplateRows: w.footer ? "1fr auto" : "1fr", gap: 12, height: "100%" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 20, alignItems: "center" }}>
        {/* 대형 선택 카드 */}
        <div
          style={{
            position: "relative",
            height: "88%",
            display: "grid",
            placeItems: "center",
            background: P.panel,
            border: `1.6px solid ${selected > 0.5 ? P.data : P.line}`,
            borderRadius: P.radiusMd,
            boxShadow: selected > 0.5 ? `0 0 24px ${P.glow}` : undefined,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <span style={{ width: 34, height: 34, borderRadius: 999, border: `2.4px solid ${P.data}`, display: "grid", placeItems: "center", color: P.data, fontWeight: 900, fontSize: 17 }}>
              ◍
            </span>
            <span style={{ fontFamily: P.fontHero, fontWeight: 900, fontSize: Math.max(28, w.h * 0.18), color: P.text, letterSpacing: "0.02em" }}>{w.primary}</span>
          </div>
          {/* 선택 브래킷 */}
          {selected > 0.5 && (
            <>
              <span style={{ position: "absolute", left: -7, top: -7, width: 16, height: 16, borderLeft: `2px solid ${P.data}`, borderTop: `2px solid ${P.data}` }} />
              <span style={{ position: "absolute", right: -7, bottom: -7, width: 16, height: 16, borderRight: `2px solid ${P.data}`, borderBottom: `2px solid ${P.data}` }} />
            </>
          )}
        </div>
        {/* 옵션 스택 */}
        <div style={{ display: "grid", gap: 14, alignContent: "center" }}>
          {w.options.map((opt, i) => {
            const appear = promoProgress(frame, w.enterAt + i * w.staggerFrames, 10);
            return (
              <div
                key={opt}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "14px 16px",
                  borderRadius: P.radiusMd,
                  background: P.panel,
                  border: `${P.hairline}px solid ${P.line}`,
                  opacity: appear,
                  transform: `translateX(${(1 - appear) * 10}px)`,
                }}
              >
                <span style={{ color: P.muted, fontSize: 13 }}>▣</span>
                <span style={{ ...labelStyle(12), color: P.text }}>{opt}</span>
              </div>
            );
          })}
        </div>
      </div>
      {w.footer && (
        <div style={{ ...labelStyle(10), justifySelf: "center", opacity: selected }}>
          <span style={{ color: P.data, marginRight: 6 }}>●</span>
          {w.footer}
        </div>
      )}
    </div>
  );
};

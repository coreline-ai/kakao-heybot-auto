// W5 리더보드 — pill 헤더 + 행(순위·이름·우측정렬 점수) + 하이라이트 행 풀폭 블루 + 리오더 상승.
// 원본 근거: KIMI-K3 분석 씬 9-11 (PROGRAM BENCH · KIMI K3 77.8 · NEW LEADER).
import React from "react";
import { interpolate } from "remotion";
import type { LeaderboardWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle, promoEase, valueStyle } from "../tokens";

// 행 i의 진입 상태 (테스트 대상 순수 함수) — stagger된 opacity 0→1
export function rowAppear(w: LeaderboardWidget, frame: number, index: number): number {
  return promoProgress(frame, w.enterAt + index * w.populateFrames, 10);
}

export const LeaderboardBody: React.FC<{ widget: LeaderboardWidget; frame: number }> = ({ widget: w, frame }) => {
  const rowH = Math.max(30, Math.min(56, (w.h - 60) / Math.max(1, w.rows.length) - 8));
  const reorderStart = w.enterAt + w.rows.length * w.populateFrames + 4;

  return (
    <div style={{ display: "grid", alignContent: "start", gap: 10, height: "100%" }}>
      {(w.header || w.headerTag) && (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {w.header && (
            <span
              style={{
                ...labelStyle(11),
                color: P.text,
                background: P.panelStrong,
                border: `${P.hairline}px solid ${P.line}`,
                borderRadius: P.radiusSm,
                padding: "4px 10px",
              }}
            >
              ◈ {w.header}
            </span>
          )}
          {w.headerTag && <span style={labelStyle(10)}>{w.headerTag}</span>}
        </div>
      )}
      <div style={{ display: "grid", gap: 8 }}>
        {w.rows.map((row, i) => {
          const appear = rowAppear(w, frame, i);
          // 리오더 — highlight 행이 최하단 슬롯에서 자기 순위 슬롯으로 상승
          const lift =
            w.reorder && row.highlight
              ? interpolate(frame, [reorderStart, reorderStart + 18], [(w.rows.length - 1 - i) * (rowH + 8), 0], promoEase)
              : 0;
          return (
            <div
              key={row.name}
              style={{
                display: "grid",
                gridTemplateColumns: "34px 1fr auto",
                alignItems: "center",
                gap: 10,
                height: rowH,
                padding: "0 14px",
                boxSizing: "border-box",
                borderRadius: P.radiusMd,
                background: row.highlight ? P.data : P.panel,
                border: `${P.hairline}px solid ${row.highlight ? P.data : P.line}`,
                boxShadow: row.highlight ? `0 0 24px ${P.glow}` : undefined,
                opacity: appear,
                transform: `translateY(${lift + (1 - appear) * 8}px)`,
                zIndex: row.highlight ? 1 : 0,
              }}
            >
              <span style={{ ...valueStyle(rowH * 0.42, row.highlight ? P.text : P.faint) }}>
                {row.highlight ? "▸" : i + 1}
              </span>
              <span
                style={{
                  fontFamily: P.fontHero,
                  fontWeight: row.highlight ? 900 : 700,
                  fontStyle: row.highlight ? "italic" : "normal",
                  fontSize: rowH * 0.44,
                  color: row.highlight ? P.text : P.muted,
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {row.name}
              </span>
              <span style={valueStyle(rowH * 0.42, row.highlight ? P.text : P.muted)}>
                {row.score.toFixed(w.scoreDecimals)}
              </span>
            </div>
          );
        })}
      </div>
      {w.footer && (
        <div style={{ ...labelStyle(10), justifySelf: "center", marginTop: 4, opacity: promoProgress(frame, reorderStart + 12, 10) }}>
          {w.footer}
        </div>
      )}
    </div>
  );
};

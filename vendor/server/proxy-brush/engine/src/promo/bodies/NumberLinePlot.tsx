// W7 수직선/ELO 플롯 — 수평 축 + tone 점 + CLOSING 점선 화살표 draw-on.
// 원본 근거: KIMI-K3 분석 씬 12 (FINAL STRETCH // GDPval — OPUS 1600 · KIMI 1668 · FABLE 1760).
import React from "react";
import type { PromoWidget } from "../schema";
import { normalizeRatio, promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "numberLinePlot" }>;

const toneColor = { data: P.data, rival: P.rival, muted: P.faint } as const;

export const NumberLinePlotBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const draw = promoProgress(frame, w.enterAt, w.drawFrames);
  const dotY = 46; // % — 점 라인 높이
  const from = w.points.find((p) => p.name === w.arrowFrom);
  const to = w.points.find((p) => p.name === w.arrowTo);
  return (
    <div style={{ position: "relative", height: "100%" }}>
      {/* 배경 dashed 행 (원본의 결승선 레인 질감) */}
      {[20, 46, 72].map((y) => (
        <div key={y} style={{ position: "absolute", left: 0, right: 0, top: `${y}%`, borderTop: `1px dashed ${P.line}` }} />
      ))}
      {/* 화살표 (SVG pathLength draw-on) */}
      {from && to && (
        <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
          <line
            x1={normalizeRatio(from.value, w.min, w.max) * 100}
            y1={dotY}
            x2={normalizeRatio(to.value, w.min, w.max) * 100}
            y2={dotY}
            stroke={P.data}
            strokeWidth={0.6}
            strokeDasharray="2 1.4"
            pathLength={1}
            strokeDashoffset={0}
            opacity={draw}
          />
        </svg>
      )}
      {w.arrowLabel && from && to && (
        <div
          style={{
            ...labelStyle(9),
            color: P.data,
            position: "absolute",
            top: `${dotY - 12}%`,
            left: `${((normalizeRatio(from.value, w.min, w.max) + normalizeRatio(to.value, w.min, w.max)) / 2) * 100}%`,
            transform: "translateX(-50%)",
            opacity: draw,
          }}
        >
          {w.arrowLabel} ▸
        </div>
      )}
      {/* 점 + 이름 + 값 */}
      {w.points.map((p, i) => {
        const x = normalizeRatio(p.value, w.min, w.max) * 100;
        const appear = promoProgress(frame, w.enterAt + i * 6, 10);
        const color = toneColor[p.tone];
        return (
          <React.Fragment key={p.name}>
            <div
              style={{
                position: "absolute",
                left: `${x}%`,
                top: `${dotY}%`,
                width: 12,
                height: 12,
                borderRadius: 999,
                background: color,
                transform: "translate(-50%, -50%)",
                boxShadow: p.tone !== "muted" ? `0 0 12px ${color}` : undefined,
                opacity: appear,
              }}
            />
            <div style={{ ...labelStyle(10), color, position: "absolute", left: `${x}%`, top: `${dotY - 20}%`, transform: "translateX(-50%)", opacity: appear }}>
              {p.name}
            </div>
            <div style={{ ...valueStyle(15, p.tone === "muted" ? P.faint : color), position: "absolute", left: `${x}%`, bottom: 0, transform: "translateX(-50%)", opacity: appear }}>
              {p.value}
            </div>
          </React.Fragment>
        );
      })}
      {/* 축 + 라벨 */}
      <div style={{ position: "absolute", left: 0, right: 0, bottom: "18%", borderTop: `${P.hairline}px solid ${P.lineStrong}` }} />
      {w.axisLabel && <div style={{ ...labelStyle(9), position: "absolute", left: 0, bottom: 0 }}>{w.axisLabel}</div>}
    </div>
  );
};

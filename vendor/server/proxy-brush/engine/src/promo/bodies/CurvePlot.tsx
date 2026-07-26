// W12 어텐션 곡선 플롯 — dashed 베이스라인 + 곡선 draw-on + × 마커 + 끝 화살표.
// 원본 근거: KIMI-K3 분석 씬 13 (Kimi Delta Attention · STANDARD ROUTE 대비 재설계 곡선).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "curvePlot" }>;

export const CurvePlotBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const draw = promoProgress(frame, w.enterAt, w.drawFrames);
  const baselineY = 32; // %
  const path = w.points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x * 100} ${p.y * 100}`).join(" ");
  const last = w.points[w.points.length - 1];
  return (
    <div style={{ position: "relative", height: "100%" }}>
      <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
        {/* dashed 베이스라인 */}
        <line x1={0} y1={baselineY} x2={100} y2={baselineY} stroke={P.lineStrong} strokeWidth={0.35} strokeDasharray="1.6 1.2" />
        {/* 곡선 draw-on */}
        <path d={path} fill="none" stroke={P.data} strokeWidth={0.9} strokeLinecap="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - draw} style={{ filter: `drop-shadow(0 0 2px ${P.glow})` }} />
        {/* × 마커 — 곡선 진행이 지나간 지점만 */}
        {w.markers.map((mi) => {
          const p = w.points[mi];
          if (!p) return null;
          const reached = draw >= (mi + 1) / w.points.length;
          return reached ? (
            <text key={mi} x={p.x * 100} y={p.y * 100 + 1.5} textAnchor="middle" fontSize="5" fontWeight="900" fill={P.data}>
              ×
            </text>
          ) : null;
        })}
        {/* 끝 화살표 */}
        {last && draw > 0.97 && (
          <text x={last.x * 100 + 1} y={last.y * 100 + 1.4} textAnchor="start" fontSize="4.6" fontWeight="900" fill={P.data}>
            ➤
          </text>
        )}
        {/* 시작점 링 */}
        {w.points[0] && (
          <circle cx={w.points[0].x * 100} cy={w.points[0].y * 100} r={1.6} fill="none" stroke={P.data} strokeWidth={0.5} />
        )}
      </svg>
      {w.baselineLabel && (
        <div style={{ ...labelStyle(9), position: "absolute", left: "8%", top: `${baselineY - 10}%`, border: `${P.hairline}px solid ${P.line}`, borderRadius: P.radiusSm, padding: "2px 6px", background: P.bg }}>
          {w.baselineLabel}
        </div>
      )}
    </div>
  );
};

// W11 노드/네트워크 그래프 — 보더 박스 + 노드 + 방사 커넥터 draw-on + pill/푸터.
// 원본 근거: KIMI-K3 분석 씬 13(KDA BLUEPRINT)·씬 27(/swarm PARALLEL AGENTS).
import React from "react";
import type { PromoWidget } from "../schema";
import { panelStyle, promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "nodeGraph" }>;

export const NodeGraphBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  return (
    <div style={panelStyle}>
      {/* 내부 미세 그리드 텍스처 */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: `linear-gradient(${P.line} 1px, transparent 1px), linear-gradient(90deg, ${P.line} 1px, transparent 1px)`,
          backgroundSize: "36px 36px",
          opacity: 0.35,
        }}
      />
      {w.pill && (
        <div
          style={{
            ...labelStyle(10),
            color: P.data,
            position: "absolute",
            top: 12,
            left: 12,
            border: `${P.hairline}px solid ${P.line}`,
            borderRadius: P.radiusSm,
            padding: "3px 8px",
            background: P.bg,
            zIndex: 2,
          }}
        >
          {w.pill}
        </div>
      )}
      {/* 커넥터 draw-on (엣지별 stagger) */}
      <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
        {w.edges.map(([a, b], ei) => {
          const na = w.nodes[a];
          const nb = w.nodes[b];
          if (!na || !nb) return null;
          const draw = promoProgress(frame, w.enterAt + ei * 5, w.drawFrames);
          return (
            <line
              key={ei}
              x1={na.x * 100}
              y1={na.y * 100}
              x2={nb.x * 100}
              y2={nb.y * 100}
              stroke={P.data}
              strokeWidth={0.4}
              pathLength={1}
              strokeDasharray={1}
              strokeDashoffset={1 - draw}
              opacity={0.8}
            />
          );
        })}
      </svg>
      {/* 노드 */}
      {w.nodes.map((n, i) => {
        const appear = promoProgress(frame, w.enterAt + i * 4, 10);
        return (
          <div
            key={`${n.label}-${i}`}
            style={{ position: "absolute", left: `${n.x * 100}%`, top: `${n.y * 100}%`, transform: "translate(-50%, -50%)", display: "grid", justifyItems: "center", gap: 4, opacity: appear }}
          >
            <span style={{ width: 10, height: 10, borderRadius: 999, background: P.data, boxShadow: `0 0 10px ${P.glow}` }} />
            <span style={{ ...labelStyle(9), color: P.muted }}>{n.label}</span>
          </div>
        );
      })}
      {w.footer && (
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 10, display: "flex", alignItems: "center", justifyContent: "center", gap: 12 }}>
          <span style={{ width: 40, height: P.hairline, background: P.line }} />
          <span style={{ ...labelStyle(11), color: P.text }}>{w.footer}</span>
          <span style={{ width: 40, height: P.hairline, background: P.line }} />
        </div>
      )}
    </div>
  );
};

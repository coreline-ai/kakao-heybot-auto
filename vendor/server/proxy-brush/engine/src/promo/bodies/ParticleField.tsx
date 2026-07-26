// W14 전문가 파티클 필드 — 결정적 스캐터 도트 활성화 + 라이브 카운터.
// 원본 근거: KIMI-K3 분석 씬 16 (896명의 전문가 중 매 순간 16명 — MoE).
import React from "react";
import type { PromoWidget } from "../schema";
import { hash01, panelStyle, promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "particleField" }>;

// 현재 활성 도트 수 (테스트 대상 순수 함수)
export function activeCount(w: W, frame: number): number {
  return Math.min(w.count, Math.floor(Math.min(w.activeTarget, w.count) * promoProgress(frame, w.enterAt, w.activateFrames)));
}

export const ParticleFieldBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const active = activeCount(w, frame);
  return (
    <div style={panelStyle}>
      {Array.from({ length: w.count }, (_, i) => {
        const x = hash01(i * 2) * 92 + 4;
        const y = hash01(i * 2 + 1) * 84 + 6;
        const on = i < active;
        return (
          <span
            key={i}
            style={{
              position: "absolute",
              left: `${x}%`,
              top: `${y}%`,
              width: on ? 5 : 3,
              height: on ? 5 : 3,
              borderRadius: 999,
              background: on ? P.data : P.track,
              boxShadow: on ? `0 0 8px ${P.glow}` : undefined,
            }}
          />
        );
      })}
      {w.counterLabel && (
        <div
          style={{
            position: "absolute",
            right: 12,
            bottom: 10,
            display: "flex",
            alignItems: "baseline",
            gap: 8,
            padding: "6px 10px",
            borderRadius: P.radiusSm,
            border: `${P.hairline}px solid ${P.line}`,
            background: P.bg,
          }}
        >
          <span style={labelStyle(9)}>{w.counterLabel}</span>
          <span style={valueStyle(16, P.data)}>{active}</span>
        </div>
      )}
    </div>
  );
};

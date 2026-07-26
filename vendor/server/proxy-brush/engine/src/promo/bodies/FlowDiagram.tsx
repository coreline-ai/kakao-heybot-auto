// W16 플로우 다이어그램 — 스텝 박스 순차 활성 (FETCH → COMPUTE → WRITE).
// 원본 근거: KIMI-K3 분석 씬 22 (GPU 커널 REWRITTEN 플로우).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "flowDiagram" }>;

// 현재 활성 스텝 인덱스 (테스트 대상 순수 함수)
export function activeStep(w: W, frame: number): number {
  const total = w.steps.length * w.stepFrames;
  return Math.min(w.steps.length - 1, Math.floor(promoProgress(frame, w.enterAt, total) * w.steps.length));
}

export const FlowDiagramBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const active = activeStep(w, frame);
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 14, height: "100%" }}>
      {w.steps.map((step, i) => {
        const isActive = i === active;
        const passed = i < active;
        return (
          <React.Fragment key={step}>
            <div
              style={{
                ...labelStyle(12),
                color: isActive ? P.text : passed ? P.muted : P.faint,
                padding: "12px 18px",
                borderRadius: P.radiusMd,
                border: `${P.hairline}px solid ${isActive ? P.data : P.line}`,
                background: isActive ? P.accentSoft : P.panel,
                boxShadow: isActive ? `0 0 18px ${P.glow}` : undefined,
              }}
            >
              {step}
            </div>
            {i < w.steps.length - 1 && <span style={{ color: passed || isActive ? P.data : P.faint, fontSize: 14 }}>▸</span>}
          </React.Fragment>
        );
      })}
    </div>
  );
};

// W21 번호 콜아웃 패널 — 헤더 + 01/02/03 리스트 + 리더 라인 draw-on + 타깃 레티클.
// 원본 근거: KIMI-K3 분석 씬 20 (OBSERVER · INSPECT — 3D CONSOLE MODEL / REAL HARDWARE REF / EMULATION LAYER).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "calloutPanel" }>;

export const CalloutPanelBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const draw = promoProgress(frame, w.enterAt, w.drawFrames);
  const panelLeft = 0.44; // 패널이 차지하는 우측 영역 시작 (rel)
  return (
    <div style={{ position: "relative", height: "100%" }}>
      {/* 타깃 레티클 + 리더 라인 */}
      {w.reticle && (
        <>
          <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
            <line
              x1={w.reticle.x * 100}
              y1={w.reticle.y * 100}
              x2={panelLeft * 100}
              y2={18}
              stroke={P.data}
              strokeWidth={0.4}
              pathLength={1}
              strokeDasharray={1}
              strokeDashoffset={1 - draw}
            />
          </svg>
          <div
            style={{
              position: "absolute",
              left: `${w.reticle.x * 100}%`,
              top: `${w.reticle.y * 100}%`,
              width: 26,
              height: 26,
              transform: "translate(-50%, -50%)",
              border: `1.4px solid ${P.data}`,
              borderRadius: 4,
              clipPath: "polygon(0 0, 30% 0, 30% 12%, 12% 12%, 12% 30%, 0 30%, 0 0, 100% 0, 100% 30%, 88% 30%, 88% 12%, 70% 12%, 70% 0, 100% 0, 100% 100%, 70% 100%, 70% 88%, 88% 88%, 88% 70%, 100% 70%, 100% 100%, 0 100%, 0 70%, 12% 70%, 12% 88%, 30% 88%, 30% 100%, 0 100%)",
              opacity: draw,
            }}
          />
        </>
      )}
      {/* 콜아웃 패널 */}
      <div
        style={{
          position: "absolute",
          left: `${panelLeft * 100}%`,
          right: 0,
          top: 0,
          bottom: 0,
          boxSizing: "border-box",
          background: P.panelStrong,
          border: `${P.hairline}px solid ${P.lineStrong}`,
          borderLeft: `2px solid ${P.data}`,
          borderRadius: P.radiusMd,
          padding: "14px 16px",
          display: "grid",
          alignContent: "start",
          gap: 12,
        }}
      >
        <div style={{ ...labelStyle(10), color: P.text }}>
          <span style={{ color: P.rival, marginRight: 6 }}>●</span>
          {w.header}
        </div>
        {w.items.map((item, i) => {
          const appear = promoProgress(frame, w.enterAt + 8 + i * 8, 8);
          return (
            <div key={item} style={{ display: "flex", alignItems: "baseline", gap: 10, opacity: appear }}>
              <span style={{ ...valueStyle(11, P.data) }}>{String(i + 1).padStart(2, "0")}</span>
              <span style={{ ...labelStyle(10), color: P.muted }}>{item}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};

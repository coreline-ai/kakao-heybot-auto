// W17 지층/터널 다이어그램 — 해치 레이어 박스 + 하단 튜브를 주행하는 발광 도트.
// 원본 근거: KIMI-K3 분석 씬 15 (10년 된 트랜스포머 지층에 새 통로).
import React from "react";
import type { PromoWidget } from "../schema";
import { panelStyle, promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "strataDiagram" }>;

export const StrataDiagramBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  return (
    <div
      style={{
        ...panelStyle,
        backgroundImage: `repeating-linear-gradient(45deg, transparent 0 7px, rgba(255,255,255,0.035) 7px 8px)`,
      }}
    >
      <div style={{ ...labelStyle(9), position: "absolute", top: 10, left: 12 }}>
        <span style={{ color: P.data }}>◈ </span>
        {w.title}
      </div>
      {/* 상단 지층 실루엣 블록 */}
      {[14, 34, 52, 70].map((left, i) => (
        <div
          key={left}
          style={{
            position: "absolute",
            left: `${left}%`,
            top: 0,
            width: `${10 + (i % 2) * 5}%`,
            height: `${22 + (i % 3) * 8}%`,
            background: P.panelStrong,
            borderBottom: `${P.hairline}px solid ${P.line}`,
          }}
        />
      ))}
      {/* 하단 통로 튜브 + 주행 도트 */}
      <div
        style={{
          position: "absolute",
          left: "5%",
          right: "5%",
          bottom: "14%",
          height: 20,
          borderRadius: 999,
          border: `1.4px solid ${P.data}`,
          boxShadow: `0 0 14px ${P.glow}, inset 0 0 10px ${P.accentSoft}`,
        }}
      >
        {Array.from({ length: w.dots }, (_, i) => {
          const t = promoProgress(frame, w.enterAt + i * Math.round(w.travelFrames / (w.dots + 1)), w.travelFrames);
          return (
            <span
              key={i}
              style={{
                position: "absolute",
                left: `${2 + t * 92}%`,
                top: "50%",
                transform: "translateY(-50%)",
                width: 9,
                height: 9,
                borderRadius: 999,
                background: P.data,
                boxShadow: `0 0 10px ${P.glow}`,
              }}
            />
          );
        })}
      </div>
    </div>
  );
};

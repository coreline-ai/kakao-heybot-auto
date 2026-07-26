// W15 오실로스코프 — 메인 chirp 파형 스크롤 + 소 레퍼런스 패널 + 하단 pill.
// 원본 근거: KIMI-K3 분석 씬 22 (GW STRAIN FEED / TEMPLATE BANK — 중력파 데이터 분석).
import React from "react";
import type { PromoWidget } from "../schema";
import { panelStyle } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "oscilloscope" }>;

// chirp 파형 — 우측으로 갈수록 주파수·진폭 증가, frame으로 위상 스크롤 (결정적)
function chirpPath(frame: number, phaseSpeed: number): string {
  const pts: string[] = [];
  for (let i = 0; i <= 100; i += 1.5) {
    const x = i / 100;
    const amp = 8 + 26 * x * x;
    const y = 50 - amp * Math.sin(2 * Math.PI * (1.5 * x + 4 * x * x) - frame * phaseSpeed);
    pts.push(`${i === 0 ? "M" : "L"} ${i} ${y.toFixed(2)}`);
  }
  return pts.join(" ");
}

const Screen: React.FC<{ label: string; frame: number; phaseSpeed: number }> = ({ label, frame, phaseSpeed }) => (
  <div style={{ ...panelStyle, background: P.bg }}>
    <div style={{ ...labelStyle(9), position: "absolute", top: 8, left: 10, zIndex: 1 }}>
      <span style={{ color: P.rival }}>● </span>
      {label}
    </div>
    {/* 중앙 십자선 */}
    <div style={{ position: "absolute", left: "50%", top: 0, bottom: 0, width: P.hairline, background: P.line }} />
    <svg style={{ position: "absolute", inset: 0 }} width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none">
      <path d={chirpPath(frame, phaseSpeed)} fill="none" stroke={P.text} strokeWidth={0.5} opacity={0.9} />
    </svg>
  </div>
);

export const OscilloscopeBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => (
  <div style={{ display: "grid", gridTemplateRows: w.pill ? "1fr auto" : "1fr", gap: 10, height: "100%" }}>
    <div style={{ display: "grid", gridTemplateColumns: w.refLabel ? "1.6fr 1fr" : "1fr", gap: 12, alignItems: "stretch" }}>
      <Screen label={w.mainLabel} frame={frame} phaseSpeed={0.11} />
      {w.refLabel && <Screen label={w.refLabel} frame={0} phaseSpeed={0} />}
    </div>
    {w.pill && (
      <div style={{ justifySelf: "start", display: "flex", alignItems: "center", gap: 6, padding: "5px 10px", borderRadius: P.radiusSm, background: P.panelStrong, border: `${P.hairline}px solid ${P.line}` }}>
        <span style={{ ...labelStyle(9), color: P.data }}>∿</span>
        <span style={labelStyle(9)}>{w.pill}</span>
      </div>
    )}
  </div>
);

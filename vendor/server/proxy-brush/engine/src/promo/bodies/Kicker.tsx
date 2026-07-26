// W23 키커 오버라인 — 좌상 대문자 모노 트래킹 + 블루 틱 + 옵션 pill.
// 원본 근거: KIMI-K3 분석 전 씬 공통 고정 슬롯 (예: "CHALLENGER // APPROACHING").
import React from "react";
import type { PromoWidget } from "../schema";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "kicker" }>;

export const KickerBody: React.FC<{ widget: W; frame: number }> = ({ widget: w }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 10, height: "100%" }}>
    {w.tick && <span style={{ width: 3, height: 14, background: P.data }} />}
    <span style={labelStyle(13)}>{w.text}</span>
    {w.pill && (
      <span style={{ ...labelStyle(10), color: P.text, background: P.data, borderRadius: P.radiusSm, padding: "3px 10px" }}>{w.pill}</span>
    )}
  </div>
);

// W31 자막 밴드 — 중앙 하단 고정 슬롯 + 어둠 그라디언트 (씬 배치용 위젯판).
// 원본 근거: KIMI-K3 분석 전 씬 공통 (하단 y≈92% 고정 베이스라인).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P } from "../tokens";

type W = Extract<PromoWidget, { type: "subtitle" }>;

export const SubtitleBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => (
  <div
    style={{
      height: "100%",
      display: "grid",
      placeItems: "end center",
      background: "linear-gradient(180deg, transparent, rgba(0,0,0,0.55))",
      opacity: promoProgress(frame, w.enterAt, 10),
    }}
  >
    <div style={{ fontFamily: P.fontValue, fontSize: 26, fontWeight: 700, color: P.text, paddingBottom: 10, textAlign: "center" }}>{w.text}</div>
  </div>
);

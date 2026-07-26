// W28 타임라인 스크러버 — 기간 압축 (2 WEEKS → 2 HOURS). 핸들 이동 + 라벨 전환.
// 원본 근거: KIMI-K3 분석 씬 23 (2주짜리 과학 코딩을 2시간에).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "timelineScrubber" }>;

export const TimelineScrubberBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const t = promoProgress(frame, w.enterAt, w.scrubFrames);
  const handleX = 6 + t * 88; // %
  return (
    <div style={{ position: "relative", height: "100%" }}>
      {/* from 라벨 — 압축되며 흐려짐 */}
      <div style={{ ...valueStyle(Math.max(16, w.h * 0.22), P.faint), position: "absolute", left: 0, top: 0, opacity: 1 - t * 0.65 }}>
        {w.fromLabel}
      </div>
      {/* 트랙 + 눈금 */}
      <div style={{ position: "absolute", left: 0, right: 0, top: "52%", height: P.hairline, background: P.lineStrong }} />
      {Array.from({ length: 13 }, (_, i) => (
        <span key={i} style={{ position: "absolute", left: `${4 + i * 7.6}%`, top: "48%", width: P.hairline, height: 8, background: P.line }} />
      ))}
      {/* 핸들 */}
      <div
        style={{
          position: "absolute",
          left: `${handleX}%`,
          top: "52%",
          transform: "translate(-50%, -50%)",
          width: 16,
          height: 16,
          borderRadius: P.radiusSm,
          background: P.data,
          boxShadow: `0 0 14px ${P.glow}`,
        }}
      />
      {/* 진행 필 */}
      <div style={{ position: "absolute", left: "4%", width: `${handleX - 4}%`, top: "52%", height: 3, transform: "translateY(-50%)", background: P.data, borderRadius: 2 }} />
      {/* to 라벨 — 블루로 등장 */}
      <div style={{ ...valueStyle(Math.max(18, w.h * 0.26), P.data), fontFamily: P.fontHero, fontWeight: 900, position: "absolute", right: 0, bottom: 0, opacity: t }}>
        {w.toLabel}
      </div>
    </div>
  );
};

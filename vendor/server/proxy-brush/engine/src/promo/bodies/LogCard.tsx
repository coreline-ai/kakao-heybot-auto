// W18 로그/기록 카드 — 헤더 양끝(제목/● REC) + 타자기 활성 엔트리 + 고스트 슬롯 + 푸터.
// 원본 근거: KIMI-K3 분석 씬 5 (SEASON 2026 · MATCH RECORD / ENTRY 001 ▸ CHALLENGER K3 SIGNED IN).
import React from "react";
import type { PromoWidget } from "../schema";
import { panelStyle, promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "logCard" }>;

// 타자기로 표시되는 활성 엔트리 부분 문자열 (테스트 대상 순수 함수)
export function typedEntry(w: W, frame: number): string {
  const n = Math.floor(w.activeEntry.length * promoProgress(frame, w.enterAt + 6, w.typeFrames));
  return w.activeEntry.slice(0, n);
}

export const LogCardBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const typed = typedEntry(w, frame);
  return (
    <div style={{ display: "grid", gridTemplateRows: w.footer ? "1fr auto" : "1fr", gap: 8, height: "100%" }}>
      <div style={{ ...panelStyle, background: P.panelStrong }}>
        {/* 헤더 — 양끝 정렬 */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderBottom: `${P.hairline}px solid ${P.line}` }}>
          <span style={{ ...labelStyle(11), color: P.text }}>{w.title}</span>
          {w.rec && (
            <span style={{ ...labelStyle(9), color: P.rival }}>
              <span style={{ marginRight: 4 }}>●</span>REC
            </span>
          )}
        </div>
        {/* 활성 엔트리 (타자기) + 고스트 슬롯 */}
        <div style={{ display: "grid", gap: 14, padding: "16px 16px" }}>
          <div style={{ ...labelStyle(11), color: P.data, letterSpacing: "0.14em" }}>
            ENTRY 001 ▸ {typed}
            <span style={{ opacity: frame % 30 < 18 ? 1 : 0 }}>▌</span>
          </div>
          {Array.from({ length: w.ghostCount }, (_, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "auto 1fr", alignItems: "center", gap: 10 }}>
              <span style={{ ...labelStyle(9), color: P.faint }}>ENTRY 00{i + 2}</span>
              <span style={{ height: P.hairline, background: P.line }} />
            </div>
          ))}
        </div>
      </div>
      {w.footer && <div style={labelStyle(9)}>{w.footer}</div>}
    </div>
  );
};

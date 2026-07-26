// W19 터미널 — 부트 라인 stagger + 프롬프트 타이핑 + 커서 블링크 + 완료 상태 라인.
// 원본 근거: KIMI-K3 분석 씬 26 (KIMI CODE — BOOT SEQUENCE / $ kimi / K3 READY).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "terminal" }>;

// 타이핑된 커맨드 부분 문자열 (테스트 대상 순수 함수)
export function typedCommand(w: W, frame: number): string {
  const start = w.enterAt + w.bootLines.length * 8 + 6;
  const n = Math.floor(w.command.length * promoProgress(frame, start, w.typeFrames));
  return w.command.slice(0, n);
}

export const TerminalBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const typed = typedCommand(w, frame);
  const done = typed.length >= w.command.length;
  const promptSize = Math.max(22, Math.min(44, w.h * 0.16));
  return (
    <div style={{ display: "grid", alignContent: "center", gap: 14, height: "100%", fontFamily: P.fontLabel }}>
      {w.header && <div style={labelStyle(10)}>{w.header}</div>}
      {w.bootLines.map((line, i) => (
        <div key={line} style={{ ...labelStyle(10), color: P.muted, opacity: promoProgress(frame, w.enterAt + i * 8, 8) }}>
          ▸ {line}
        </div>
      ))}
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <span style={{ color: P.data, fontSize: promptSize, fontWeight: 800 }}>{w.prompt}</span>
        <span style={{ color: P.text, fontSize: promptSize, fontWeight: 800, letterSpacing: "0.04em" }}>{typed}</span>
        <span
          style={{
            width: promptSize * 0.55,
            height: promptSize * 1.05,
            background: P.data,
            alignSelf: "center",
            opacity: frame % 30 < 18 ? 1 : 0,
          }}
        />
      </div>
      <div style={{ height: P.hairline, background: P.lineStrong, width: "56%" }} />
      {w.status && (
        <div style={{ ...labelStyle(12), color: P.data, opacity: done ? promoProgress(frame, w.enterAt + w.bootLines.length * 8 + 6 + w.typeFrames, 10) : 0 }}>
          ▸ {w.status} ◂
        </div>
      )}
    </div>
  );
};

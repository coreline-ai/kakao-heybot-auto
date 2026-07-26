// W27 스플릿플랩 보드 — 글자 타일이 결정적 문자 순환 후 정착 (P R I C I N G).
// 원본 근거: KIMI-K3 분석 씬 24 (TICKET BOOTH — PRICING 스플릿플랩).
import React from "react";
import type { PromoWidget } from "../schema";
import { P, valueStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "splitFlap" }>;

// i번째 글자의 현재 표시 문자 — 정착 전에는 결정적 순환 문자 (테스트 대상 순수 함수)
export function flapChar(w: W, frame: number, i: number): string {
  const target = w.text[i] ?? " ";
  if (target === " ") return " ";
  const settleAt = w.enterAt + i * w.staggerFrames + w.flipFrames;
  if (frame >= settleAt) return target;
  if (frame < w.enterAt + i * w.staggerFrames) return " ";
  return String.fromCharCode(65 + ((frame * 7 + i * 13) % 26));
}

export const SplitFlapBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const chars = w.text.split("");
  const tile = Math.min(w.h * 0.72, (w.w - (chars.length - 1) * 8) / chars.length);
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, height: "100%" }}>
      {chars.map((_, i) => {
        const c = flapChar(w, frame, i);
        if (c === " " && (w.text[i] ?? " ") === " ") return <span key={i} style={{ width: tile * 0.4 }} />;
        return (
          <span
            key={i}
            style={{
              position: "relative",
              width: tile * 0.82,
              height: tile,
              display: "grid",
              placeItems: "center",
              borderRadius: P.radiusSm,
              background: P.panelStrong,
              border: `${P.hairline}px solid ${P.lineStrong}`,
              ...valueStyle(tile * 0.62),
              fontFamily: P.fontHero,
              fontWeight: 900,
              overflow: "hidden",
            }}
          >
            {c}
            {/* 플랩 분할선 */}
            <span style={{ position: "absolute", left: 0, right: 0, top: "50%", height: P.hairline, background: "rgba(0,0,0,0.5)" }} />
          </span>
        );
      })}
    </div>
  );
};

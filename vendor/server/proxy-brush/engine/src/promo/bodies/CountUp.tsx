// W8 카운트업 / W9 배수 — 오도미터 히어로 숫자 + prefix/suffix 블루 + 언더라인 룰 + 캡션.
// 원본 근거: KIMI-K3 분석 씬 6(2.8T)·씬 10-11(0.0→42.0)·씬 14(×6.3)·씬 23(87%).
import React from "react";
import type { CountUpWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

// 현재 프레임의 표시 값 (테스트 대상 순수 함수) — ease-out으로 from→to 단조 진행
export function countUpValue(w: CountUpWidget, frame: number): number {
  const t = promoProgress(frame, w.enterAt, w.countFrames);
  return w.from + (w.to - w.from) * t;
}

export const CountUpBody: React.FC<{ widget: CountUpWidget; frame: number }> = ({ widget: w, frame }) => {
  const value = countUpValue(w, frame);
  const heroSize = Math.min(w.h * 0.62, w.w * 0.28);
  return (
    <div style={{ display: "grid", justifyItems: "center", alignContent: "center", gap: 10, height: "100%" }}>
      <div style={{ display: "flex", alignItems: "baseline", whiteSpace: "nowrap" }}>
        {w.prefix && (
          <span style={{ ...valueStyle(heroSize * 0.72, P.data), fontWeight: 900, marginRight: 4 }}>{w.prefix}</span>
        )}
        <span
          style={{
            ...valueStyle(heroSize),
            fontFamily: P.fontHero,
            fontWeight: 900,
            letterSpacing: "-0.01em",
            textShadow: "none", // 크리스프 원칙 — 원본 계측 텍스트(2.8T 등)는 글로우 0
          }}
        >
          {value.toFixed(w.decimals)}
        </span>
        {w.suffix && (
          <span
            style={{
              ...valueStyle(heroSize * 0.9, w.suffixAccent ? P.data : P.text),
              fontFamily: P.fontHero,
              fontWeight: 900,
            }}
          >
            {w.suffix}
          </span>
        )}
      </div>
      {w.rule && <div style={{ width: "62%", height: P.hairline, background: P.lineStrong }} />}
      {w.caption && <div style={labelStyle(12)}>{w.caption}</div>}
    </div>
  );
};

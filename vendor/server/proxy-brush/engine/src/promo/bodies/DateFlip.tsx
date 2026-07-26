// W33 날짜/콘솔 플립 — 글자 그룹 rotateX 플립-인 + 블루 강조부 + 키커.
// 원본 근거: KIMI-K3 분석 씬 28 (SEASON OPENER — JUL 27).
import React from "react";
import { interpolate } from "remotion";
import type { PromoWidget } from "../schema";
import { P, labelStyle, promoEase } from "../tokens";

type W = Extract<PromoWidget, { type: "dateFlip" }>;

const FlipChar: React.FC<{ c: string; at: number; frames: number; frame: number; size: number; color: string }> = ({ c, at, frames, frame, size, color }) => {
  const rot = interpolate(frame, [at, at + frames], [88, 0], promoEase);
  const op = interpolate(frame, [at, at + frames * 0.6], [0, 1], promoEase);
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: P.fontHero,
        fontWeight: 900,
        fontSize: size,
        color,
        transform: `perspective(600px) rotateX(${rot}deg)`,
        transformOrigin: "center 70%",
        opacity: op,
      }}
    >
      {c}
    </span>
  );
};

export const DateFlipBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const size = Math.max(40, Math.min(w.h * 0.52, w.w * 0.16));
  const chars = [...w.text.split("").map((c) => ({ c, accent: false })), ...(w.accent ? [{ c: " ", accent: false }, ...w.accent.split("").map((c) => ({ c, accent: true }))] : [])];
  return (
    <div style={{ display: "grid", justifyItems: "center", alignContent: "center", gap: 10, height: "100%" }}>
      {w.kicker && <div style={labelStyle(11)}>{w.kicker}</div>}
      <div style={{ display: "flex" }}>
        {chars.map((ch, i) => (
          <FlipChar key={i} c={ch.c} at={w.enterAt + i * 3} frames={w.flipFrames} frame={frame} size={size} color={ch.accent ? P.data : P.text} />
        ))}
      </div>
    </div>
  );
};

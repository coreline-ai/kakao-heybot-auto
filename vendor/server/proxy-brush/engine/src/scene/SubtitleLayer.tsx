// 하단 자막 — 화이트 배경을 해치지 않는 미니멀 스타일 (옅은 화이트 필 + 잉크 텍스트 + 흰 헤일로).
// cue는 frame 단위 [from, to), 단어 하이라이트가 cue 구간을 따라 이동한다.
import React from "react";
import { interpolate, useVideoConfig } from "remotion";
import { clamp, easeDraw } from "../lib/easing";
import type { Cue, SubtitleStyle } from "../schema";

export const SubtitleLayer: React.FC<{ frame: number; cues: Cue[]; style?: SubtitleStyle }> = ({ frame, cues, style }) => {
  const { width } = useVideoConfig();
  const scale = width / 1920;
  const active = cues.find((c) => frame >= c.from && frame < c.to);
  if (!active) return null;

  const op = Math.min(
    interpolate(frame, [active.from, active.from + 6], [0, 1], clamp),
    interpolate(frame, [active.to - 6, active.to], [1, 0], clamp),
  );
  const rise = interpolate(frame, [active.from, active.from + 8], [10, 0], easeDraw);
  const words = active.text.split(" ");
  const highlightStart = active.from + 4;
  const highlightEnd = Math.max(highlightStart + 1, active.to - 4);
  const cur = Math.max(0, Math.min(words.length - 1, Math.floor(interpolate(frame, [highlightStart, highlightEnd], [0, words.length], clamp))));

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: (style?.bottom ?? 56) * scale,
        display: "flex",
        justifyContent: "center",
        padding: `0 ${(style?.paddingX ?? 160) * scale}px`,
        zIndex: 40,
        opacity: op,
        transform: `translateY(${rise * scale}px)`,
      }}
    >
      <div
        style={{
          maxWidth: (style?.maxWidth ?? 1320) * scale,
          background: style?.background ?? "rgba(255,255,255,0.5)",
          border: style?.border ?? "1px solid rgba(58,70,87,0.10)",
          borderRadius: 18 * scale,
          padding: `${(style?.paddingY ?? 12) * scale}px ${30 * scale}px`,
          boxShadow: "0 6px 20px rgba(58,70,87,0.06)",
          backdropFilter: "blur(3px)",
          fontFamily: '"Apple SD Gothic Neo", "Noto Sans KR", sans-serif',
          fontSize: (style?.fontSize ?? 38) * scale,
          fontWeight: 800,
          lineHeight: 1.38,
          letterSpacing: `${-0.2 * scale}px`,
          textAlign: "center",
        }}
      >
        {words.map((w, i) => (
          <span
            key={i}
            style={{
              margin: `0 ${5 * scale}px`,
              color: i === cur ? (style?.highlightColor ?? "#7a5fc0") : (style?.color ?? "#3a4657"),
              textShadow: "0 1px 3px rgba(255,255,255,0.85), 0 0 10px rgba(255,255,255,0.7)",
            }}
          >
            {w}
          </span>
        ))}
      </div>
    </div>
  );
};

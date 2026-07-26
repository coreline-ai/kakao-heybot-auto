// 상단 주제 타이틀 — 골드 kicker(좌우 구분선) + 굵은 제목. 첫 줄 첫 단어는 배경 인상색으로 강조.
import React from "react";
import { interpolate, useVideoConfig } from "remotion";
import { easeDraw } from "../lib/easing";
import type { TopTitle } from "../schema";

export const TitleLayer: React.FC<{ frame: number; spec: TopTitle }> = ({ frame, spec }) => {
  const { width } = useVideoConfig();
  const scale = width / 1920;
  const accent = spec.accent ?? "#b8862f";
  const fontSize = (spec.fontSize ?? 60) * scale;
  const kickerFontSize = (spec.kickerFontSize ?? 20) * scale;
  const op = interpolate(frame, [spec.enterAt, spec.enterAt + 18], [0, 1], easeDraw);
  const rise = interpolate(frame, [spec.enterAt, spec.enterAt + 20], [14, 0], easeDraw);
  if (op <= 0.001) return null;

  const kickerText = (
    <span style={{ fontSize: kickerFontSize, fontWeight: 850, letterSpacing: "5px", color: accent, textTransform: "uppercase", whiteSpace: "nowrap" }}>
      {spec.kicker}
    </span>
  );

  return (
    <div
      style={{
        position: "absolute",
        left: (spec.x ?? 110) * scale,
        top: (spec.y ?? 74) * scale,
        width: (spec.width ?? 700) * scale,
        zIndex: 23,
        pointerEvents: "none",
        opacity: op,
        transform: `translateY(${rise * scale}px)`,
        fontFamily: '"Apple SD Gothic Neo", "Noto Sans KR", Inter, sans-serif',
        textAlign: spec.align,
        padding: spec.wash ? `${8 * scale}px ${12 * scale}px ${10 * scale}px` : 0,
        borderRadius: 18 * scale,
        background: spec.wash ? "linear-gradient(90deg, rgba(251,250,246,0.70), rgba(251,250,246,0.42))" : "transparent",
        backdropFilter: spec.wash ? "blur(1.5px)" : undefined,
      }}
    >
      {spec.kicker && (
        <div style={{ display: "flex", alignItems: "center", gap: 16 * scale, marginBottom: 16 * scale }}>
          {spec.align === "center" ? (
            <>
              <span style={{ flex: 1, minWidth: 70 * scale, height: 2 * scale, background: accent, opacity: 0.48 }} />
              {kickerText}
              <span style={{ flex: 1, minWidth: 70 * scale, height: 2 * scale, background: accent, opacity: 0.48 }} />
            </>
          ) : spec.align === "left" ? (
            <>
              <span style={{ width: 36 * scale, height: 2 * scale, background: accent, opacity: 0.82 }} />
              {kickerText}
              <span style={{ flex: 1, minWidth: 34 * scale, height: 2 * scale, background: accent, opacity: 0.35 }} />
            </>
          ) : (
            <>
              <span style={{ flex: 1, minWidth: 34 * scale, height: 2 * scale, background: accent, opacity: 0.35 }} />
              {kickerText}
              <span style={{ width: 36 * scale, height: 2 * scale, background: accent, opacity: 0.82 }} />
            </>
          )}
        </div>
      )}
      {spec.lines.map((ln, i) => {
        const lineStyle: React.CSSProperties = {
          fontSize,
          fontWeight: 950,
          lineHeight: 1.14,
          color: spec.color ?? "#26313D",
          letterSpacing: `${-1.4 * scale}px`,
          whiteSpace: "nowrap",
          textShadow: spec.color
            ? "0 2px 16px rgba(0,0,0,0.62), 0 0 22px rgba(85,205,255,0.20)"
            : "0 1px 2px rgba(255,255,255,0.86), 0 0 16px rgba(255,255,255,0.58)",
        };
        if (i === 0 && spec.firstWordColor) {
          const sp = ln.indexOf(" ");
          const first = sp === -1 ? ln : ln.slice(0, sp);
          const rest = sp === -1 ? "" : ln.slice(sp);
          return (
            <div key={i} style={lineStyle}>
              <span style={{ color: spec.firstWordColor }}>{first}</span>
              {rest}
            </div>
          );
        }
        return (
          <div key={i} style={lineStyle}>
            {ln}
          </div>
        );
      })}
    </div>
  );
};

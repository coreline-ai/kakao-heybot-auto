// W29 코너 프레이밍 브래킷 — 4코너 + 상단 양끝 라벨 + 중앙 라벨 (풀블리드 푸티지 프레임).
// 원본 근거: KIMI-K3 분석 씬 20-21 (WUXIA RPG · PRESS START).
import React from "react";
import type { PromoWidget } from "../schema";
import { promoProgress } from "../shared";
import { P, labelStyle } from "../tokens";

type W = Extract<PromoWidget, { type: "frameBrackets" }>;

const CORNER = 26;
const corner = (pos: React.CSSProperties, borders: React.CSSProperties): React.CSSProperties => ({
  position: "absolute",
  width: CORNER,
  height: CORNER,
  ...pos,
  ...borders,
});

export const FrameBracketsBody: React.FC<{ widget: W; frame: number }> = ({ widget: w, frame }) => {
  const appear = promoProgress(frame, w.enterAt, 12);
  const b = `2px solid ${P.text}`;
  return (
    <div style={{ position: "relative", height: "100%", opacity: appear }}>
      <span style={corner({ left: 0, top: 0 }, { borderLeft: b, borderTop: b })} />
      <span style={corner({ right: 0, top: 0 }, { borderRight: b, borderTop: b })} />
      <span style={corner({ left: 0, bottom: 0 }, { borderLeft: b, borderBottom: b })} />
      <span style={corner({ right: 0, bottom: 0 }, { borderRight: b, borderBottom: b })} />
      {w.topLeft && <div style={{ ...labelStyle(10), position: "absolute", top: -26, left: 0 }}>{w.topLeft}</div>}
      {w.topRight && (
        <div style={{ ...labelStyle(10), position: "absolute", top: -26, right: 0, borderBottom: `${P.hairline}px solid ${P.lineStrong}`, paddingBottom: 3 }}>
          {w.topRight}
        </div>
      )}
      {w.centerLabel && (
        <div
          style={{
            ...labelStyle(15),
            color: P.text,
            position: "absolute",
            left: "50%",
            bottom: "16%",
            transform: "translateX(-50%)",
            display: "flex",
            alignItems: "center",
            gap: 12,
            opacity: promoProgress(frame, w.enterAt + 10, 12),
          }}
        >
          <span style={{ color: P.data }}>•</span>
          {w.centerLabel}
          <span style={{ color: P.data }}>•</span>
        </div>
      )}
    </div>
  );
};

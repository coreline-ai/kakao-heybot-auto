// W1 게이지 — needle(니들 스윕 + 값 카운트업) / fill-arc(아크 채움 + 틱 스포크 점등 + 골드 테일).
// 원본 근거: KIMI-K3 분석 씬 6(계량 2.8T)·씬 25-26(캐시 히트율 40→90%).
import React from "react";
import type { GaugeWidget } from "../schema";
import { normalizeRatio, promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

// 반원 게이지 좌표 — 180°(좌) → 0°(우), 위쪽 반원
function polar(cx: number, cy: number, r: number, angleDeg: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
}

function arcPath(cx: number, cy: number, r: number, fromDeg: number, toDeg: number): string {
  const a = polar(cx, cy, r, fromDeg);
  const b = polar(cx, cy, r, toDeg);
  const large = Math.abs(fromDeg - toDeg) > 180 ? 1 : 0;
  return `M ${a.x} ${a.y} A ${r} ${r} 0 ${large} 1 ${b.x} ${b.y}`;
}

// 현재 프레임의 게이지 표시 상태 (테스트 대상 순수 함수)
export function gaugeState(w: GaugeWidget, frame: number): { ratio: number; shownValue: number } {
  const target = normalizeRatio(w.value, w.min, w.max);
  const t = promoProgress(frame, w.enterAt, w.sweepFrames);
  const ratio = target * t;
  return { ratio, shownValue: w.min + (w.max - w.min) * ratio };
}

export const GaugeBody: React.FC<{ widget: GaugeWidget; frame: number }> = ({ widget: w, frame }) => {
  const { ratio, shownValue } = gaugeState(w, frame);
  const size = Math.min(w.w, w.h * 1.6);
  const cx = size / 2;
  const r = size * 0.42;
  const cy = r + size * 0.06;
  const stroke = w.kind === "fill-arc" ? Math.max(8, size * 0.045) : Math.max(2, size * 0.012);
  const angle = 180 - ratio * 180; // 바늘/채움 현재 각

  const ticks = Array.from({ length: w.ticks + 1 }, (_, i) => {
    const a = 180 - (i / Math.max(1, w.ticks)) * 180;
    const lit = w.kind === "fill-arc" && i / Math.max(1, w.ticks) <= ratio;
    const inner = polar(cx, cy, r + stroke * 0.9, a);
    const outer = polar(cx, cy, r + stroke * 0.9 + size * 0.05, a);
    return { key: i, inner, outer, lit };
  });

  const isGold = (deg: number) => w.goldTail && deg < 180 * 0.18; // 우측 끝 ~18% 골드 테일

  return (
    <div style={{ position: "relative", width: "100%", height: "100%", display: "grid", placeItems: "center" }}>
      <svg width={size} height={cy + size * 0.1} viewBox={`0 0 ${size} ${cy + size * 0.1}`}>
        {/* 트랙 아크 */}
        <path d={arcPath(cx, cy, r, 180, 0)} fill="none" stroke={P.track} strokeWidth={stroke} strokeLinecap="round" />
        {/* 채움 아크 (fill-arc) — 골드 테일은 끝 구간을 별도 세그먼트로 */}
        {w.kind === "fill-arc" && ratio > 0.001 && (
          <>
            <path
              d={arcPath(cx, cy, r, 180, Math.max(angle, isGold(angle) ? 180 * 0.18 : angle))}
              fill="none"
              stroke={P.data}
              strokeWidth={stroke}
              strokeLinecap="round"
              style={{ filter: `drop-shadow(0 0 ${stroke}px ${P.glow})` }}
            />
            {isGold(angle) && (
              <path
                d={arcPath(cx, cy, r, 180 * 0.18, angle)}
                fill="none"
                stroke={P.cta}
                strokeWidth={stroke}
                strokeLinecap="round"
              />
            )}
          </>
        )}
        {/* 눈금/스포크 */}
        {ticks.map((t) => (
          <line
            key={t.key}
            x1={t.inner.x}
            y1={t.inner.y}
            x2={t.outer.x}
            y2={t.outer.y}
            stroke={t.lit ? P.data : P.lineStrong}
            strokeWidth={t.lit ? 3 : 1.5}
            strokeLinecap="round"
          />
        ))}
        {/* 바늘 (needle) */}
        {w.kind === "needle" && (
          <>
            <line
              x1={cx}
              y1={cy}
              x2={polar(cx, cy, r * 0.86, angle).x}
              y2={polar(cx, cy, r * 0.86, angle).y}
              stroke={P.data}
              strokeWidth={Math.max(2.5, size * 0.014)}
              strokeLinecap="round"
              style={{ filter: `drop-shadow(0 0 6px ${P.glow})` }}
            />
            <circle cx={cx} cy={cy} r={Math.max(4, size * 0.02)} fill={P.data} />
          </>
        )}
      </svg>
      {/* 중앙 네스트 값 — fill-arc는 아크 안쪽, needle은 하단 */}
      <div
        style={{
          position: "absolute",
          left: 0,
          right: 0,
          top: w.kind === "fill-arc" ? cy * 0.52 : cy + size * 0.02,
          display: "grid",
          justifyItems: "center",
          gap: 6,
        }}
      >
        <div style={valueStyle(Math.max(24, size * 0.16))}>
          {shownValue.toFixed(w.max - w.min >= 50 ? 0 : 1)}
          {w.unit && <span style={{ color: P.data }}>{w.unit}</span>}
        </div>
        {w.caption && (
          <div style={{ ...labelStyle(10), border: `${P.hairline}px solid ${P.line}`, borderRadius: P.radiusSm, padding: "3px 8px" }}>
            {w.caption}
          </div>
        )}
      </div>
    </div>
  );
};

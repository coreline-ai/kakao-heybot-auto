// W2 진행 바 / W3 VS 대결 바 — 좌→우 필 + 상단 눈금 + 우단 골드 라벨 / 주체 vs 라이벌 + VS 배지.
// 원본 근거: KIMI-K3 분석 씬 7(CONTEXT STAMINA · 1M TOKENS)·씬 8(VS 88.3)·씬 29(BONUS +26%).
import React from "react";
import type { StatBarWidget } from "../schema";
import { normalizeRatio, promoProgress } from "../shared";
import { P, labelStyle, valueStyle } from "../tokens";

// 현재 프레임의 바 채움 비율 (테스트 대상 순수 함수)
export function statBarFill(w: StatBarWidget, frame: number): number {
  return normalizeRatio(w.value, 0, w.max) * promoProgress(frame, w.enterAt, w.fillFrames);
}

const Track: React.FC<{ fillPct: number; color: string; height: number; glow?: boolean }> = ({ fillPct, color, height, glow }) => (
  <div style={{ position: "relative", height, borderRadius: height / 2, background: P.track, overflow: "visible" }}>
    <div
      style={{
        position: "absolute",
        left: 0,
        top: 0,
        bottom: 0,
        width: `${fillPct * 100}%`,
        borderRadius: height / 2,
        background: color, // var() 팔레트와 호환 — 알파 이어붙임 금지 (P6-A)
        boxShadow: glow ? `0 0 ${height * 1.4}px ${P.glow}` : undefined,
      }}
    />
  </div>
);

const VsBadge: React.FC<{ size: number }> = ({ size }) => (
  <div style={{ position: "relative", width: size * 1.5, height: size, flex: "0 0 auto" }}>
    <div style={{ position: "absolute", inset: 0, transform: "skewX(-12deg)", display: "flex" }}>
      <div style={{ flex: 1, background: P.data }} />
      <div style={{ flex: 1, background: P.rival }} />
    </div>
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "grid",
        placeItems: "center",
        fontFamily: P.fontHero,
        fontStyle: "italic",
        fontWeight: 900,
        fontSize: size * 0.62,
        color: P.text,
      }}
    >
      VS
    </div>
  </div>
);

export const StatBarBody: React.FC<{ widget: StatBarWidget; frame: number }> = ({ widget: w, frame }) => {
  const fill = statBarFill(w, frame);
  const barH = Math.max(10, Math.min(22, w.h * 0.16));

  if (w.mode === "vs") {
    const rivalMax = Math.max(w.max, ...w.rivals.map((r) => r.value));
    return (
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto 1fr", alignItems: "center", gap: 18, height: "100%" }}>
        <div>
          {w.name && <div style={{ ...labelStyle(13), color: P.data, fontStyle: "italic", marginBottom: 8 }}>{w.name}</div>}
          <Track fillPct={fill} color={P.data} height={barH} glow />
          <div style={{ ...valueStyle(Math.max(26, w.h * 0.34), P.data), marginTop: 10 }}>
            {(normalizeRatio(w.value, 0, w.max) === 0 ? 0 : w.value * (fill / normalizeRatio(w.value, 0, w.max))).toFixed(1)}
          </div>
        </div>
        <VsBadge size={Math.max(28, w.h * 0.3)} />
        <div style={{ display: "grid", gap: 12, alignContent: "center" }}>
          {w.rivals.map((r) => (
            <div key={r.name}>
              <Track fillPct={normalizeRatio(r.value, 0, rivalMax) * promoProgress(frame, w.enterAt, w.fillFrames)} color={P.faint} height={barH * 0.7} />
              <div style={{ ...labelStyle(10), textAlign: "right", marginTop: 4 }}>
                {r.name} · {r.value.toFixed(1)}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // progress 모드
  return (
    <div style={{ display: "grid", alignContent: "center", gap: 8, height: "100%" }}>
      {w.ticks.length > 0 && (
        <div style={{ position: "relative", height: 18 }}>
          {w.ticks.map((t) => (
            <div
              key={t.label}
              style={{
                position: "absolute",
                left: `${normalizeRatio(t.at, 0, w.max) * 100}%`,
                transform: "translateX(-50%)",
                display: "grid",
                justifyItems: "center",
                gap: 2,
              }}
            >
              <span style={labelStyle(10)}>{t.label}</span>
              <span style={{ width: 1, height: 5, background: P.lineStrong }} />
            </div>
          ))}
        </div>
      )}
      <Track fillPct={fill} color={P.data} height={barH} glow />
      {w.endLabel && (
        <div style={{ ...labelStyle(13), color: P.cta, justifySelf: "end", letterSpacing: "0.14em" }}>{w.endLabel}</div>
      )}
    </div>
  );
};

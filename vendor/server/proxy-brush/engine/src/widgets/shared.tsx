// 위젯 공용 시스템 — 화이트 카드 시각 언어(종이색·잉크·muted accent)는 참조 시스템 팔레트를 채택.
// CardShell: kicker/title/글리프 헤더 + 구분선 + 바디 + caption 푸터. 등장 모션(enterAt) 포함.
import React from "react";
import { Easing, interpolate } from "remotion";
import type { Widget } from "../schema";

export const T = {
  surface: "rgba(255,255,255,0.86)",
  surfaceStrong: "rgba(255,255,255,0.96)",
  ink: "#26313D",
  muted: "#5D6876",
  faint: "#8A95A3",
  line: "rgba(38,49,61,0.15)",
  lineStrong: "rgba(38,49,61,0.24)",
  shadow: "rgba(38,49,61,0.12)",
  lavender: "#7A5FC0",
  sage: "#4F8C6D",
  blue: "#407B91",
  clay: "#A65F54",
  ochre: "#B8862F",
  red: "#B85E54",
} as const;

export const ACCENTS = [T.lavender, T.sage, T.blue, T.clay, T.ochre] as const;
export const pickAccent = (w: Widget, index: number) => w.accent ?? ACCENTS[index % ACCENTS.length];

const ease = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const, easing: Easing.out(Easing.cubic) };

const GLYPHS: Record<Widget["type"], string> = {
  stat: "◆", text: "T", donut: "◎", bars: "↗",
  FlowDiagram: "⌬", TimelineStepper: "⌬", PersonAvatar: "⌬",
  DataTable: "◌", ProcessStepCard: "▣", WarningCard: "▣",
  ChatBubble: "⌘", CompareBars: "↗", BulletList: "T", QuoteText: "T", Headline: "T",
};

export const CardShell: React.FC<{ widget: Widget; frame: number; index: number; children: React.ReactNode }> = ({ widget: w, frame, index, children }) => {
  const accent = pickAccent(w, index);
  const appear = interpolate(frame, [w.enterAt, w.enterAt + 14], [0, 1], ease);
  const rise = interpolate(frame, [w.enterAt, w.enterAt + 18], [14, 0], ease);
  if (appear <= 0.001) return null;
  const pad = Math.max(15, Math.min(24, Math.round(Math.min(w.w, w.h) * 0.08)));
  const iconSize = Math.max(32, Math.min(42, Math.round(w.h * 0.22)));
  return (
    <div
      style={{
        position: "absolute",
        left: w.x,
        top: w.y,
        width: w.w,
        height: w.h,
        padding: pad,
        boxSizing: "border-box",
        overflow: "hidden",
        borderRadius: Math.max(18, Math.min(28, Math.round(w.h * 0.13))),
        background: `linear-gradient(145deg, ${T.surfaceStrong}, ${T.surface})`,
        border: `1.4px solid ${T.line}`,
        boxShadow: `0 16px 40px ${T.shadow}, inset 0 1px 0 rgba(255,255,255,0.86)`,
        color: T.ink,
        fontFamily: '"Apple SD Gothic Neo", "Noto Sans KR", Inter, sans-serif',
        opacity: appear,
        transform: `translateY(${rise}px) scale(${0.99 + appear * 0.01})`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, minHeight: iconSize }}>
        <div style={{ minWidth: 0 }}>
          {w.kicker && (
            <div style={{ fontSize: 10.5, fontWeight: 900, letterSpacing: "1.8px", textTransform: "uppercase", color: accent, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {w.kicker}
            </div>
          )}
          <div style={{ marginTop: 2, fontSize: Math.max(16, Math.min(22, Math.round(w.h * 0.105))), lineHeight: 1.04, fontWeight: 940, letterSpacing: "-0.45px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {w.title}
          </div>
        </div>
        <div style={{ width: iconSize, height: iconSize, borderRadius: 999, flex: "0 0 auto", display: "grid", placeItems: "center", color: accent, background: "rgba(255,255,255,0.72)", border: `1px solid ${T.lineStrong}`, boxShadow: `0 6px 15px ${T.shadow}`, fontSize: Math.round(iconSize * 0.44), fontWeight: 950 }}>
          {GLYPHS[w.type]}
        </div>
      </div>
      <div style={{ height: 1, margin: "11px 0 14px", background: `linear-gradient(90deg, ${T.lineStrong}, rgba(38,49,61,0.04))` }} />
      <div style={{ position: "absolute", left: pad, right: pad, top: pad + iconSize + 27, bottom: w.caption ? pad + 22 : pad }}>{children}</div>
      {w.caption && (
        <div style={{ position: "absolute", left: pad, right: pad, bottom: Math.max(10, pad - 4), display: "flex", alignItems: "center", gap: 7, color: T.muted, fontSize: 10.5, fontWeight: 780, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          <span style={{ width: 5.5, height: 5.5, borderRadius: 999, background: accent, flex: "0 0 auto" }} />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{w.caption}</span>
        </div>
      )}
    </div>
  );
};

// ---------- 공용 아톰 ----------

export const Chip: React.FC<{ label: string; accent: string; detail?: string; value?: string | number; tone?: "ok" | "warn" | "danger" }> = ({ label, accent, detail, value, tone }) => {
  const color = tone === "danger" ? T.red : tone === "warn" ? T.ochre : accent;
  return (
    <div style={{ minWidth: 0, borderRadius: 12, border: `1px solid ${T.line}`, background: "rgba(255,255,255,0.62)", padding: "7px 9px", boxShadow: "inset 0 1px 0 rgba(255,255,255,0.72)" }}>
      <div style={{ color, fontSize: 13, lineHeight: 1.05, fontWeight: 940, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{value ?? label}</div>
      {detail && <div style={{ marginTop: 3, color: T.muted, fontSize: 10, lineHeight: 1.05, fontWeight: 760, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{detail}</div>}
    </div>
  );
};

export const Node: React.FC<{ label: string; accent: string }> = ({ label, accent }) => (
  <span style={{ position: "relative", zIndex: 1, width: 34, height: 34, borderRadius: 999, display: "grid", placeItems: "center", background: "rgba(255,255,255,.72)", border: `1.4px solid ${accent}`, color: accent, fontWeight: 920, fontSize: 12, whiteSpace: "nowrap", overflow: "hidden" }}>
    {label}
  </span>
);

export const Ring: React.FC<{ pct: number; accent: string }> = ({ pct, accent }) => {
  const r = 32;
  const c = 2 * Math.PI * r;
  return (
    <div style={{ height: "100%", display: "grid", placeItems: "center" }}>
      <svg width="92" height="92" viewBox="0 0 92 92">
        <circle cx="46" cy="46" r={r} fill="none" stroke={T.line} strokeWidth="10" />
        <circle cx="46" cy="46" r={r} fill="none" stroke={accent} strokeWidth="10" strokeLinecap="round" strokeDasharray={`${(c * pct) / 100} ${c}`} transform="rotate(-90 46 46)" />
        <text x="46" y="51" textAnchor="middle" fontSize="18" fontWeight="900" fill={T.ink}>{Math.round(pct)}%</text>
      </svg>
    </div>
  );
};

export const HBar: React.FC<{ label: string; value: number; accent: string }> = ({ label, value, accent }) => (
  <div style={{ display: "grid", gridTemplateColumns: "58px 1fr 34px", alignItems: "center", gap: 8, fontSize: 11, color: T.muted, fontWeight: 820 }}>
    <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</span>
    <i style={{ height: 10, borderRadius: 99, background: "rgba(38,49,61,.08)", overflow: "hidden" }}>
      <b style={{ display: "block", height: "100%", width: `${Math.max(0, Math.min(100, value))}%`, borderRadius: 99, background: accent }} />
    </i>
    <span>{Math.round(value)}%</span>
  </div>
);

// 프로모 위젯 디자인 토큰 — 테마 시스템 (P6-A).
// 팔레트는 CSS 변수로 주입되고, P는 var() 참조(기본 dark-navy fallback)라서
// 위젯 32종 코드는 무수정으로 테마를 갈아입는다. 형태·타입·모션 상수는 테마 무관.
// dark-navy: KIMI-K3 분석 계측값 (모사 계보 보존용 기본값)
// gamji-gold: 감지금니(紺紙金泥) — 쪽빛 감지 + 금니(金泥) 주선 + 주사(朱砂) 낙관 (오리지널 계보)
import type React from "react";
import { Easing } from "remotion";

export const PROMO_THEMES = {
  "dark-navy": {
    data: "#4a7fff",
    cta: "#f5b73c",
    rival: "#e2483d",
    bg: "#0a0e1a",
    text: "#ffffff",
    muted: "rgba(255,255,255,0.6)",
    faint: "rgba(255,255,255,0.35)",
    line: "rgba(255,255,255,0.14)",
    lineStrong: "rgba(255,255,255,0.24)",
    panel: "rgba(255,255,255,0.045)",
    panelStrong: "rgba(255,255,255,0.08)",
    track: "rgba(255,255,255,0.10)",
    glow: "rgba(74,127,255,0.45)",
    glowSoft: "rgba(190,210,255,0.4)", // 히어로 타이포·빔 광채
    beamA: "rgba(190,210,255,0.95)", // light-sweep 빔 상단
    beamB: "rgba(120,150,255,0.85)", // light-sweep 빔 하단
    spot: "rgba(120,150,255,0.13)", // 스포트라이트 콘
    wash: "rgba(74,127,255,0.12)", // 주 앰비언트 워시
    washWarmA: "rgba(180,60,50,0.10)", // warm 틴트 좌
    washWarmB: "rgba(74,127,255,0.09)", // warm 틴트 우
    accentSoft: "rgba(74,127,255,0.15)", // 활성 박스 필·인셋
    ctaSoft: "rgba(245,183,60,0.35)", // WINNER 스탬프 그림자
    buntAlt: "rgba(120,150,255,0.7)", // 가랜드 3색째
    veil: "rgb(214,222,238)", // 플래시 노출 리프트 베일
  },
  "gamji-gold": {
    data: "#dfae4a",
    cta: "#cf4a2f",
    rival: "#8fa3c8",
    bg: "#100e1e",
    text: "#f5eede",
    muted: "rgba(245,238,222,0.62)",
    faint: "rgba(245,238,222,0.35)",
    line: "rgba(245,238,222,0.14)",
    lineStrong: "rgba(245,238,222,0.24)",
    panel: "rgba(245,238,222,0.05)",
    panelStrong: "rgba(245,238,222,0.09)",
    track: "rgba(245,238,222,0.10)",
    glow: "rgba(223,174,74,0.45)",
    glowSoft: "rgba(235,205,130,0.4)",
    beamA: "rgba(240,215,150,0.95)",
    beamB: "rgba(223,174,74,0.85)",
    spot: "rgba(235,205,130,0.12)",
    wash: "rgba(223,174,74,0.11)",
    washWarmA: "rgba(207,74,47,0.10)",
    washWarmB: "rgba(223,174,74,0.09)",
    accentSoft: "rgba(223,174,74,0.15)",
    ctaSoft: "rgba(207,74,47,0.35)",
    buntAlt: "rgba(235,205,130,0.7)",
    veil: "rgb(238,228,205)",
  },
} as const;

export type PromoTheme = keyof typeof PROMO_THEMES;
export type PromoPaletteKey = keyof (typeof PROMO_THEMES)["dark-navy"];

export const PALETTE_KEYS = Object.keys(PROMO_THEMES["dark-navy"]) as PromoPaletteKey[];

// 컴포지션 루트에 얹는 CSS 변수 정의 — 팔레트 주입의 단일 지점
export function themeStyle(theme: PromoTheme): React.CSSProperties {
  const palette = PROMO_THEMES[theme];
  return Object.fromEntries(PALETTE_KEYS.map((k) => [`--pw-${k}`, palette[k]])) as React.CSSProperties;
}

const varRef = (k: PromoPaletteKey): string => `var(--pw-${k}, ${PROMO_THEMES["dark-navy"][k]})`;

export const P = {
  // 팔레트 — CSS 변수 참조 (themeStyle 미적용 시 dark-navy fallback)
  data: varRef("data"),
  cta: varRef("cta"),
  rival: varRef("rival"),
  bg: varRef("bg"),
  text: varRef("text"),
  muted: varRef("muted"),
  faint: varRef("faint"),
  line: varRef("line"),
  lineStrong: varRef("lineStrong"),
  panel: varRef("panel"),
  panelStrong: varRef("panelStrong"),
  track: varRef("track"),
  glow: varRef("glow"),
  glowSoft: varRef("glowSoft"),
  beamA: varRef("beamA"),
  beamB: varRef("beamB"),
  spot: varRef("spot"),
  wash: varRef("wash"),
  washWarmA: varRef("washWarmA"),
  washWarmB: varRef("washWarmB"),
  accentSoft: varRef("accentSoft"),
  ctaSoft: varRef("ctaSoft"),
  buntAlt: varRef("buntAlt"),
  veil: varRef("veil"),

  // 형태 — 테마 무관
  radiusSm: 4,
  radiusMd: 8,
  hairline: 1,

  // 타입 — 히어로=콘덴스드 볼드 / 라벨=대문자 모노 트래킹 / 수치=tabular
  fontHero: '"Avenir Next Condensed", "Arial Narrow", "Apple SD Gothic Neo", sans-serif',
  fontLabel: '"SF Mono", ui-monospace, "JetBrains Mono", Menlo, monospace',
  fontValue: 'Inter, "Helvetica Neue", "Apple SD Gothic Neo", sans-serif',
} as const;

// 모션 — 전 위젯 공통 ease-out 정착. 오버슈트는 강조 배지 전용(개별 구현).
export const promoEase = {
  extrapolateLeft: "clamp" as const,
  extrapolateRight: "clamp" as const,
  easing: Easing.out(Easing.cubic),
};

export const SETTLE_FRAMES = 12;

// 라벨(대문자 모노 트래킹) 공통 스타일
export const labelStyle = (size = 13): React.CSSProperties => ({
  fontFamily: P.fontLabel,
  fontSize: size,
  fontWeight: 700,
  letterSpacing: "0.22em",
  textTransform: "uppercase" as const,
  color: P.muted,
  whiteSpace: "nowrap",
});

// 수치(tabular 우측정렬) 공통 스타일
export const valueStyle = (size: number, color: string = P.text): React.CSSProperties => ({
  fontFamily: P.fontValue,
  fontSize: size,
  fontWeight: 800,
  fontVariantNumeric: "tabular-nums",
  color,
});

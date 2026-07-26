// 자연 분위기 이펙트 6종 — 전체 이미지의 색감 정착이 끝난 뒤부터 서서히 나타난다.
// 전부 seed 기반 deterministic (Math.random 금지). 자막 영역(H*0.72 아래)에는 입자를 올리지 않는다.
import React, { useId } from "react";
import { interpolate } from "remotion";
import { clamp } from "../lib/easing";
import type { NaturalEffects } from "../schema";

const particleCoord = (seed: number, i: number, axis: "x" | "y") => {
  const n = Math.sin(seed * 17.13 + i * (axis === "x" ? 41.91 : 27.77)) * 10000;
  return n - Math.floor(n);
};

// 이펙트 가시 창 계약 — on 램프(startFrame..+RAMP_UP, drawFrame 기준)와
// off 램프(routesDuration-36..-18, frame 기준)의 단일 정의.
// prewash 가 없으면 drawFrame≈frame 이므로 onStart >= offEnd 이면 두 램프가 겹치지 않아
// alpha 가 전 프레임 0 이 된다(이펙트 미표시). 저opacity(예: mist 0.035) 이펙트의 표시/소멸은
// 골든 픽셀 diff(2% 임계)로는 감지되지 않으므로, 이 계약을 단위 테스트로 고정해 램프 공식의
// 무의식적 변경을 잡는다 (FIELD-LOG 2026-07-13 EffectLayer 타이밍 사각지대).
export const EFFECT_RAMP_UP = 24;
export const effectWindow = (startFrame: number, routesDuration: number) => {
  const onStart = startFrame;
  const onFull = startFrame + EFFECT_RAMP_UP;
  const offStart = routesDuration - 36;
  const offEnd = routesDuration - 18;
  return { onStart, onFull, offStart, offEnd, hasVisibleWindow: onStart < offEnd };
};

type Props = {
  frame: number;
  drawFrame: number;
  startFrame: number;
  routesDuration: number;
  W: number;
  H: number;
  spec: NaturalEffects;
};

export const EffectLayer: React.FC<Props> = ({ frame, drawFrame, startFrame, routesDuration, W, H, spec }) => {
  const baseOpacity = Math.max(0, Math.min(0.1, spec.opacity));
  const seed = spec.seed;
  const id = `natural-${useId().replace(/[:]/g, "")}`;
  const win = effectWindow(startFrame, routesDuration);
  const on = interpolate(drawFrame, [win.onStart, win.onFull], [0, 1], clamp);
  // 자연 이펙트는 outro 전에 완전히 종료한다. 워시 위에 안개·글로우가 남으면
  // 장면이 다시 흐려지거나 커지는 것처럼 보일 수 있다.
  const off = interpolate(frame, [win.offStart, win.offEnd], [1, 0], clamp);
  const alpha = baseOpacity * on * off;
  if (alpha <= 0.001) return null;

  const t = frame / 30;
  const safeBottom = H * 0.72;
  const pulse = (i: number, speed = 1) => 0.58 + 0.42 * Math.sin(t * speed + i * 1.73);

  const mist = Array.from({ length: 9 }, (_, i) => {
    const x = W * (0.08 + particleCoord(seed, i, "x") * 0.84) + Math.sin(t * 0.23 + i) * 12;
    const yBase = H * (0.12 + particleCoord(seed + 3, i, "y") * 0.48);
    const y = ((yBase - t * (6 + i * 0.4)) % safeBottom + safeBottom) % safeBottom;
    return <ellipse key={i} cx={x} cy={y} rx={72 + i * 7} ry={18 + (i % 3) * 8} fill="rgba(255,255,255,0.82)" opacity={alpha * (0.55 + pulse(i, 0.35) * 0.35)} />;
  });

  const dust = Array.from({ length: 16 }, (_, i) => {
    const x = W * (0.12 + particleCoord(seed + 5, i, "x") * 0.76) + Math.sin(t * 0.36 + i) * 9;
    const y = H * (0.16 + particleCoord(seed + 8, i, "y") * 0.52) + Math.cos(t * 0.21 + i) * 7;
    const r = 2.2 + particleCoord(seed, i, "y") * 3.4;
    return <circle key={i} cx={x} cy={Math.min(y, safeBottom)} r={r} fill="#fff2bd" opacity={alpha * (0.42 + pulse(i, 0.72) * 0.38)} />;
  });

  const sparkles = Array.from({ length: 5 }, (_, i) => {
    const x = W * (0.24 + particleCoord(seed + 10, i, "x") * 0.52);
    const y = H * (0.36 + particleCoord(seed + 11, i, "y") * 0.28);
    const o = alpha * (0.72 + pulse(i, 1.8) * 0.88);
    return (
      <g key={i} opacity={o}>
        <circle cx={x} cy={y} r={2.4 + i * 0.25} fill="#f6fffb" />
        <path d={`M ${x - 12} ${y} L ${x + 12} ${y} M ${x} ${y - 12} L ${x} ${y + 12}`} stroke="#e9fff8" strokeWidth="1.7" strokeLinecap="round" />
      </g>
    );
  });

  const wind = [0, 1, 2].map((_, i) => {
    const y = H * (0.26 + i * 0.16) + Math.sin(t * 0.26 + i) * 14;
    const x = -W * 0.12 + ((t * (26 + i * 8) + i * 190) % (W * 1.24));
    return (
      <path
        key={i}
        d={`M ${x} ${y} C ${x + W * 0.14} ${y - 36}, ${x + W * 0.26} ${y + 34}, ${x + W * 0.42} ${y - 4}`}
        fill="none"
        stroke={i === 1 ? "#f6fff0" : "#dcefcf"}
        strokeWidth={i === 1 ? 4 : 3}
        strokeLinecap="round"
        opacity={alpha * (0.62 - i * 0.1)}
      />
    );
  });

  const stars = Array.from({ length: 6 }, (_, i) => {
    const x = W * (0.16 + particleCoord(seed + 18, i, "x") * 0.68);
    const y = H * (0.10 + particleCoord(seed + 19, i, "y") * 0.32);
    const o = alpha * (0.75 + pulse(i, 1.25) * 0.95);
    return (
      <g key={i} opacity={o}>
        <circle cx={x} cy={y} r={2.6 + particleCoord(seed, i, "x") * 2.3} fill="#ffffff" />
        <path d={`M ${x - 8} ${y} L ${x + 8} ${y} M ${x} ${y - 8} L ${x} ${y + 8}`} stroke="#fff8d5" strokeWidth="1.4" strokeLinecap="round" />
      </g>
    );
  });

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{ position: "absolute", inset: 0, zIndex: 18, pointerEvents: "none" }}>
      <defs>
        <filter id={`${id}-soft`} x="-16%" y="-16%" width="132%" height="132%">
          <feGaussianBlur stdDeviation={spec.kind === "meadowWind" ? 1.1 : 5.8} />
        </filter>
        <radialGradient id={`${id}-glow`} cx="52%" cy="45%" r="65%">
          <stop offset="0%" stopColor="rgba(255,205,142,0.58)" />
          <stop offset="55%" stopColor="rgba(255,178,108,0.20)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
        <linearGradient id={`${id}-night`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(102,121,183,0.30)" />
          <stop offset="60%" stopColor="rgba(255,255,255,0)" />
        </linearGradient>
      </defs>

      {spec.kind === "mist" && <g filter={`url(#${id}-soft)`}>{mist}</g>}
      {spec.kind === "forestDust" && (
        <g filter={`url(#${id}-soft)`}>
          <ellipse cx={W * 0.34 + Math.sin(t * 0.2) * 10} cy={H * 0.38} rx={W * 0.18} ry={H * 0.035} fill="#fff0b9" opacity={alpha * 0.7} transform={`rotate(-18 ${W * 0.34} ${H * 0.38})`} />
          {dust}
        </g>
      )}
      {spec.kind === "streamSparkle" && (
        <g>
          <path d={`M ${W * 0.15} ${H * 0.52 + Math.sin(t * 0.8) * 6} C ${W * 0.36} ${H * 0.48}, ${W * 0.54} ${H * 0.61}, ${W * 0.85} ${H * 0.55}`} fill="none" stroke="#ecfffb" strokeWidth="6" strokeLinecap="round" opacity={alpha * 0.48} filter={`url(#${id}-soft)`} />
          {sparkles}
        </g>
      )}
      {spec.kind === "meadowWind" && <g filter={`url(#${id}-soft)`}>{wind}</g>}
      {spec.kind === "sunsetGlow" && (
        <g>
          <rect x="0" y="0" width={W} height={H} fill={`url(#${id}-glow)`} opacity={alpha * (1.0 + Math.sin(t * 0.32) * 0.2)} />
          <path d={`M ${W * 0.12} ${H * 0.58} C ${W * 0.32} ${H * 0.55}, ${W * 0.58} ${H * 0.61}, ${W * 0.88} ${H * 0.57}`} fill="none" stroke="#fff4dd" strokeWidth="5" strokeLinecap="round" opacity={alpha * 0.48} filter={`url(#${id}-soft)`} />
        </g>
      )}
      {spec.kind === "starTwinkle" && (
        <g>
          <rect x="0" y="0" width={W} height={H} fill={`url(#${id}-night)`} opacity={alpha * (0.6 + Math.sin(t * 0.18) * 0.1)} />
          {stars}
          {spec.endFadeOpacity && (
            <rect x="0" y="0" width={W} height={H} fill="#fbfaf6"
              opacity={interpolate(drawFrame, [routesDuration - 60, routesDuration], [0, spec.endFadeOpacity], clamp)} />
          )}
        </g>
      )}
    </svg>
  );
};

// 2단계 수묵화 리빌: ① 붓이 지나간 자리만 배경을 연하게(faint) 드러내고
// ② 완성 후 전체가 또렷하게 발현(develop)된다. prewash(인트로 예고)와 outro(종이 dissolve)도 담당.
// 수식·구조는 참조 시스템의 튜닝 결과를 채택 — 임의 변경 시 골든 diff가 깨진다.
import React, { useId } from "react";
import { AbsoluteFill, Img, interpolate, staticFile } from "remotion";
import { clamp, easeDevelop, easeTravel, sharedProgress } from "../lib/easing";
import { toPath, type Point } from "../lib/geometry";
import type { Scene, Stroke } from "../schema";

type Props = {
  scene: Scene;
  image: string; // 배경 그림 (public/ 기준 상대경로, routes meta.image)
  strokes: Stroke[]; // brushDynamics 적용 후
  penInvisibleAfter: number;
  routesDuration: number; // routes meta.durationInFrames
  frame: number;
  drawFrame: number; // frame - introDelay (prewash 동안 드로잉 지연)
  W: number;
  H: number;
  preserveSourceColor?: boolean;
};

type RevealTimingInput = Pick<Props, "scene" | "strokes" | "penInvisibleAfter" | "routesDuration">;

export const getRevealTiming = ({ scene, strokes, penInvisibleAfter, routesDuration }: RevealTimingInput) => {
  const integratedDevelop = scene.completionMode === "integrated-develop";
  const lastStrokeEnd = strokes.reduce((max, stroke) => Math.max(max, stroke.end), 0);
  const developStart = integratedDevelop && lastStrokeEnd > 0 ? lastStrokeEnd : penInvisibleAfter;
  const developEnd = scene.developFrames != null ? developStart + scene.developFrames : routesDuration - 4;
  return {
    developStart,
    developEnd,
    colorSettleEnd: developEnd + scene.colorSettleFrames,
  };
};

type CompletionVisualInput = RevealTimingInput & {drawFrame: number};

const phaseProgress = (frame: number, start: number, end: number, easing: typeof clamp) =>
  end <= start ? (frame >= end ? 1 : 0) : interpolate(frame, [start, end], [0, 1], easing);

export const getCompletionVisualState = ({
  scene,
  strokes,
  penInvisibleAfter,
  routesDuration,
  drawFrame,
}: CompletionVisualInput) => {
  const timing = getRevealTiming({scene, strokes, penInvisibleAfter, routesDuration});
  const integratedDevelop = scene.completionMode === "integrated-develop";
  const developOpacity = scene.completionMode === "masked-hold"
    ? 0
    : phaseProgress(drawFrame, timing.developStart, timing.developEnd,
                    integratedDevelop ? clamp : easeDevelop);
  const colorProgress = integratedDevelop
    ? phaseProgress(drawFrame, timing.developEnd, timing.colorSettleEnd, easeDevelop)
    : 0;
  return {
    ...timing,
    integratedDevelop,
    developOpacity,
    colorProgress,
    saturation: 1 + colorProgress * 0.12,
    contrast: 1,
    brightness: 1,
    revealOpacity: integratedDevelop
      ? scene.faint
      : scene.completionMode === "develop"
        ? scene.faint * (1 - developOpacity)
        : scene.faint,
  };
};

export const RevealLayer: React.FC<Props> = ({ scene, image, strokes, penInvisibleAfter, routesDuration, frame, drawFrame, W, H, preserveSourceColor = false }) => {
  const patId = `brushpat-${useId().replace(/[:]/g, "")}`;
  const prewashPatId = `prewashpat-${useId().replace(/[:]/g, "")}`;
  const introDelayFrames = frame - drawFrame;
  const prewashImage = scene.prewashImage ?? image;

  const explicitFadeOutFrames = Math.min(scene.prewashFadeOutFrames, introDelayFrames);
  const prewashAlpha = introDelayFrames > 0
    ? explicitFadeOutFrames > 0
      ? interpolate(frame, [0, explicitFadeOutFrames], [scene.prewashOpacity, 0], clamp)
      : interpolate(
          frame,
          [0, Math.min(scene.prewashHoldFrames, introDelayFrames), introDelayFrames],
          [scene.prewashOpacity, scene.prewashOpacity, 0],
          clamp,
        )
    : 0;

  // integrated-develop는 붓 커서가 사라질 때까지 기다리지 않고 실제 마지막 획이
  // 끝난 직후 시작한다. 농도 상승 시간을 충분히 확보해 빠른 번쩍임을 방지한다.
  const completion = getCompletionVisualState({
    scene,
    strokes,
    penInvisibleAfter,
    routesDuration,
    drawFrame,
  });
  const {
    integratedDevelop,
    developEnd: dEnd,
    colorSettleEnd,
    developOpacity,
    revealOpacity,
  } = completion;
  // 전체 이미지가 완성된 뒤 밝기는 건드리지 않고 채도·대비만 천천히 깊어진다.
  const finalColorFilter = `saturate(${preserveSourceColor ? 1 : completion.saturation}) contrast(${completion.contrast}) brightness(${completion.brightness})`;
  // 첫 정지 프레임부터 장면을 식별할 수 있는 희미한 가이드 이미지.
  // 동일 이미지의 완성 마스크가 차오를수록 자연스럽게 가려지므로 별도 팝업/교차 페이드가 없다.
  const previewOpacity = scene.previewOpacity * (1 - developOpacity);

  const outroFrames = Math.max(0, Math.round(scene.outroFadeFrames));

  // 자연 카메라 모션(naturalEffects.parallaxScale > 1일 때만).
  // 마스크·완성 이미지에 같은 변환을 적용해 리빌 중에도 붓 획과 원본이 어긋나지 않는다.
  // 마지막 outro 직전에서 멈추므로 씬 경계의 종이 dissolve와 줌이 겹치지 않는다.
  const parallaxScale = scene.naturalEffects?.parallaxScale ?? 1;
  let parallaxCssTransform = "none";
  if (parallaxScale > 1) {
    const motionEnd = Math.max(1, scene.durationInFrames - outroFrames - 8);
    const motionFrame = Math.min(drawFrame, motionEnd);
    const p = interpolate(motionFrame, [0, motionEnd], [0, 1], easeTravel);
    const scale = 1 + (Math.min(1.03, parallaxScale) - 1) * p;
    const seed = scene.naturalEffects?.seed ?? 1;
    const dx = Math.sin((motionFrame + seed * 11) * 0.012) * 5.5 * p;
    const dy = Math.cos((motionFrame + seed * 7) * 0.010) * 4.5 * p;
    parallaxCssTransform = `translate(${dx}px, ${dy}px) scale(${scale})`;
  }

  // Sequence의 실제 마지막 프레임은 durationInFrames - 1이다.
  // 보간 종점도 마지막 실재 프레임에 맞춰 순수 종이 화면으로 정확히 수렴시킨다.
  const outroEnd = Math.max(0, scene.durationInFrames - 1);
  const outroAlpha = outroFrames > 0
    ? interpolate(frame, [Math.max(0, scene.durationInFrames - outroFrames), outroEnd], [0, 1], easeDevelop)
    : 0;

  const { faint, edgeFeather, linearDraw } = scene;

  const strokePaths = (stroke: "mask" | "pattern") =>
    strokes.map((s) => (
      <path
        key={s.id}
        d={toPath(s.points as Point[])}
        fill="none"
        stroke={stroke === "mask" ? "#fff" : `url(#${patId})`}
        strokeWidth={s.width}
        strokeLinecap="round"
        strokeLinejoin="round"
        pathLength={1}
        strokeDasharray={1}
        strokeDashoffset={1 - sharedProgress(drawFrame, s.start, s.end, linearDraw)}
        opacity={drawFrame < s.start ? 0 : 1}
      />
    ));

  return (
    <>
      {/* 단일 SVG: 이미지 패턴을 한 번만 디코드해 prewash·faint 리빌·develop이 공유 */}
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{
        position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none",
        transform: parallaxScale > 1 ? parallaxCssTransform : "none",
        transformOrigin: "center center",
      }}>
        <defs>
          <pattern id={patId} patternUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
            <image href={staticFile(image)} x="0" y="0" width={W} height={H} preserveAspectRatio="none" />
          </pattern>
          {prewashAlpha > 0.001 && (
            <pattern id={prewashPatId} patternUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
              <image href={staticFile(prewashImage)} x="0" y="0" width={W} height={H} preserveAspectRatio="none" />
            </pattern>
          )}
          {prewashAlpha > 0.001 && scene.prewashBlur > 0 && (
            <filter id={`pw-${patId}`} x="-8%" y="-8%" width="116%" height="116%">
              <feGaussianBlur stdDeviation={scene.prewashBlur} />
            </filter>
          )}
          {/* edgeFeather: 스트로크를 흰색 마스크로 그리고 마스크에만 블러 —
              이미지 내용은 선명 유지, 리빌 "가장자리만" 붓 질감처럼 부드럽게 */}
          {(edgeFeather > 0 || integratedDevelop) && (
            <mask id={`fm-${patId}`} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
              <g filter={edgeFeather > 0 ? `url(#fb-${patId})` : undefined}>{strokePaths("mask")}</g>
              {/* 통합 develop: 이미 드러난 M=1 영역은 그대로 두고 미커버 영역만
                  d + M(1-d)로 채운다. 동일 이미지를 두 번 합성하지 않는다. */}
              {integratedDevelop && (
                <rect x="0" y="0" width={W} height={H} fill="#fff" opacity={developOpacity} />
              )}
            </mask>
          )}
          {edgeFeather > 0 && (
            <filter id={`fb-${patId}`} x="-8%" y="-8%" width="116%" height="116%">
              <feGaussianBlur stdDeviation={edgeFeather} />
            </filter>
          )}
        </defs>

        {/* prewash: 씬 시작에만 흐린 전체 화면을 잠깐 보여준 뒤 사라짐 (첫 화면 공백 방지) */}
        {prewashAlpha > 0.001 && (
          <rect x="0" y="0" width={W} height={H} fill={`url(#${prewashPatId})`} opacity={prewashAlpha}
            filter={scene.prewashBlur > 0 ? `url(#pw-${patId})` : undefined} />
        )}

        {/* preview underlay: 펜이 시작되기 전에도 빈 종이 대신 장면의 희미한 실루엣을 보여준다. */}
        {previewOpacity > 0.001 && (
          <rect x="0" y="0" width={W} height={H} fill={`url(#${patId})`} opacity={previewOpacity} />
        )}

        {/* 1단계 faint 리빌 — masked-hold는 마지막 스트로크 상태를 그대로 유지한다.
            integrated-develop는 같은 마스크 안에서 누락 부분만 100%로 채운다.
            동일 RGBA 이미지를 두 레이어로 교차합성하면 중간 알파가 낮아져 밝기 펄스가 생기므로
            펜 프로필에서는 develop 레이어를 사용하지 않는다.
            완성 프레임에서 mask/rect를 언마운트하면 병렬 Chromium의 SVG paint가 무효화되어
            종이만 캡처될 수 있으므로 전체 렌더 동안 DOM 구조를 고정한다. */}
        {(edgeFeather > 0 || integratedDevelop) ? (
          <rect x="0" y="0" width={W} height={H} fill={`url(#${patId})`} mask={`url(#fm-${patId})`}
            opacity={revealOpacity}
            style={{ filter: integratedDevelop ? finalColorFilter : undefined }} />
        ) : (
          <g opacity={faint * (1 - developOpacity)}>{strokePaths("pattern")}</g>
        )}

      </svg>

      {/* 2단계 develop — develop 모드에서만 보인다. masked-hold에서는 opacity 0으로 유지한다.
          SVG <image> 대신 Remotion <Img>를 사용한다.
          Img는 이미지 로딩을 delayRender에 연결하므로 병렬 Chromium이 decode/paint 전에
          프레임을 캡처하지 않는다. frame 0부터 상시 마운트해 DOM 교체도 없앤다. */}
      <Img
        src={staticFile(image)}
        style={{
          position: "absolute",
          inset: 0,
          zIndex: 10,
          width: W,
          height: H,
          objectFit: "fill",
          opacity: scene.completionMode === "develop" ? developOpacity : 0,
          transform: parallaxScale > 1 ? parallaxCssTransform : "none",
          transformOrigin: "center center",
          pointerEvents: "none",
        }}
      />

      {/* outro: 씬 끝 종이 dissolve */}
      {outroAlpha > 0.001 && (
        <AbsoluteFill
          style={{
            zIndex: 80,
            pointerEvents: "none",
            opacity: Math.min(1, Math.max(0, outroAlpha * scene.outroWashOpacity)),
            background:
              "radial-gradient(circle at 22% 18%, rgba(255,255,255,0.90), rgba(251,250,246,0.72) 36%, transparent 62%), linear-gradient(180deg, rgba(255,255,253,0.96), rgba(248,245,237,0.98))",
            backdropFilter: scene.outroBlur > 0 ? `blur(${scene.outroBlur}px)` : undefined,
          }}
        />
      )}
    </>
  );
};

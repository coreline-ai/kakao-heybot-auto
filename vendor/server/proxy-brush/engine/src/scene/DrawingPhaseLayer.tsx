import React, { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, Img, interpolate, staticFile } from "remotion";
import { clamp } from "../lib/easing";
import { RoutesDataSchema, type Brush, type DrawingPhase, type RoutesData } from "../schema";
import { CursorLayer } from "./CursorLayer";
import { ProgressiveRevealLayer } from "./ProgressiveRevealLayer";
import { TexturedPaintRevealLayer } from "./TexturedPaintRevealLayer";

export function phaseOpacity(frame: number, phase: DrawingPhase): number {
  if (phase.fadeOutFrom == null || phase.fadeOutTo == null) return 1;
  return interpolate(frame, [phase.fadeOutFrom, phase.fadeOutTo], [1, 0], clamp);
}

export function phaseOutroOpacity(
  frame: number,
  durationInFrames: number,
  outroFadeFrames: number,
  outroWashOpacity: number,
): number {
  const frames = Math.max(0, Math.round(outroFadeFrames));
  if (frames === 0) return 0;
  const end = Math.max(0, durationInFrames - 1);
  const progress = interpolate(frame, [Math.max(0, durationInFrames - frames), end], [0, 1], clamp);
  return Math.min(1, Math.max(0, progress * outroWashOpacity));
}

type Props = {
  sceneId: string;
  phases: DrawingPhase[];
  frame: number;
  W: number;
  H: number;
  fallbackBrush?: Brush;
  outroFadeFrames?: number;
  outroWashOpacity?: number;
  outroBlur?: number;
  thumbnailPoster?: boolean;
};

/** outline과 paint route/image를 한 번에 로드·decode한 뒤 독립 커서로 재생한다. */
export const DrawingPhaseLayer: React.FC<Props> = ({
  sceneId, phases, frame, W, H, fallbackBrush,
  outroFadeFrames = 0, outroWashOpacity = 0.88, outroBlur = 0,
  thumbnailPoster = false,
}) => {
  const [data, setData] = useState<RoutesData[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [handle] = useState(() => delayRender(`drawing-phases:${phases.map((p) => p.routes).join("|")}`));

  useEffect(() => {
    let alive = true;
    Promise.all(phases.map(async (phase) => {
      const response = await fetch(staticFile(phase.routes));
      if (!response.ok) throw new Error(`${phase.kind}:${phase.routes}: HTTP ${response.status}`);
      const parsed = RoutesDataSchema.parse(await response.json());
      parsed.strokes.sort((a, b) => a.start - b.start);
      const image = new Image();
      image.decoding = "sync";
      image.src = staticFile(parsed.meta.image);
      await image.decode();
      return parsed;
    })).then((loaded) => {
      if (alive) setData(loaded);
      continueRender(handle);
    }).catch((reason: unknown) => {
      if (alive) setError(String(reason));
      continueRender(handle);
    });
    return () => { alive = false; };
  }, [handle, phases]);

  if (error) {
    return <div style={{ zIndex: 90, color: "#b45a4a", fontFamily: "monospace", fontSize: 28,
      position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
      drawingPhases 로드 실패 — scene={sceneId} — {error}
    </div>;
  }
  if (!data) return null;

  const outroOpacity = phaseOutroOpacity(
    frame,
    data[0]?.meta.durationInFrames ?? 0,
    outroFadeFrames,
    outroWashOpacity,
  );
  const thumbnailImage = data[1]?.meta.image ?? data[0]?.meta.image;
  // 첫 장면 완성 이미지는 9f(0.30초) 동안 종이 배경으로 선형 페이드한다.
  // 페이드가 끝난 다음 route의 펜 외곽선 → 브러시 채색만 보인다.
  const thumbnailOpacity = thumbnailPoster
    ? interpolate(frame, [0, 9], [0.68, 0], clamp)
    : 0;

  return <>
    {/* Codex/브라우저 영상 카드는 MP4 표지가 아니라 재생 v:0의 0프레임을 쓴다.
        첫 씬의 완성 장면만 f0에 두고 0.30초 안에 지운다. */}
    {thumbnailImage && thumbnailOpacity > 0.001 && (
      <Img
        src={staticFile(thumbnailImage)}
        style={{
          position: "absolute", inset: 0, zIndex: 5, width: W, height: H,
          objectFit: "fill", opacity: thumbnailOpacity, pointerEvents: "none",
        }}
      />
    )}
    {phases.map((phase, index) => {
      const routes = data[index];
      const opacity = phaseOpacity(frame, phase);
      return <React.Fragment key={`${phase.kind}:${phase.routes}`}>
        {phase.kind === "paint" ? (
          <TexturedPaintRevealLayer image={routes.meta.image} strokes={routes.strokes} frame={frame}
            W={W} H={H} zIndex={phase.zIndex} opacity={opacity} edgeFeather={phase.edgeFeather} />
        ) : (
          <ProgressiveRevealLayer image={routes.meta.image} strokes={routes.strokes} frame={frame}
            W={W} H={H} zIndex={phase.zIndex} opacity={opacity} edgeFeather={phase.edgeFeather} />
        )}
        {opacity > 0 && <CursorLayer frame={frame} drawFrame={frame} strokes={routes.strokes}
          penInvisibleAfter={routes.meta.penInvisibleAfter} linearDraw={true}
          brush={phase.cursor ?? fallbackBrush} W={W} H={H} />}
      </React.Fragment>;
    })}
    {outroOpacity > 0.001 && (
      <AbsoluteFill style={{
        zIndex: 80,
        pointerEvents: "none",
        opacity: outroOpacity,
        background:
          "radial-gradient(circle at 22% 18%, rgba(255,255,255,0.90), rgba(251,250,246,0.72) 36%, transparent 62%), linear-gradient(180deg, rgba(255,255,253,0.96), rgba(248,245,237,0.98))",
        backdropFilter: outroBlur > 0 ? `blur(${outroBlur}px)` : undefined,
      }} />
    )}
  </>;
};

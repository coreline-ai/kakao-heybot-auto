// 씬 조립 — routes 데이터 로드와 타임라인 파생만 하고, 그리기는 레이어에 위임한다.
// 레이어 순서: 종이 → 단일/다단계/랜덤 reveal → effect → cursor → widget/title/subtitle.
import React, { useEffect, useMemo, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { buildDynamicStrokes } from "../lib/dynamics";
import {
  RandomTouchRoutesDataSchema,
  RoutesDataSchema,
  type Brush,
  type RandomTouchRoutesData,
  type RoutesData,
  type Scene,
} from "../schema";
import { CosmicRandomBrushLayer } from "./CosmicRandomBrushLayer";
import { CursorLayer } from "./CursorLayer";
import { DrawingPhaseLayer } from "./DrawingPhaseLayer";
import { EffectLayer } from "./EffectLayer";
import { FullBleedPresentationLayer } from "./FullBleedPresentationLayer";
import { getRevealTiming, RevealLayer } from "./RevealLayer";
import { SubtitleLayer } from "./SubtitleLayer";
import { TitleLayer } from "./TitleLayer";
import { WidgetLayer } from "./WidgetLayer";

// 종이 질감 — 참조 시스템과 동일 값 (골든 파리티 전제, 임의 변경 금지)
const PAPER_TEXTURE =
  "radial-gradient(circle at 20% 16%, rgba(255,255,255,0.5), transparent 32%), radial-gradient(circle at 74% 72%, rgba(90,80,60,0.04), transparent 46%)";

export const BrushScene: React.FC<{ scene: Scene; paper: string; brush?: Brush }> = ({ scene, paper, brush }) => {
  const frame = useCurrentFrame();
  const { width: W, height: H } = useVideoConfig();
  const [data, setData] = useState<RoutesData | RandomTouchRoutesData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [handle] = useState(() => (scene.routes ? delayRender(`routes:${scene.id}`) : null));

  useEffect(() => {
    if (!scene.routes || handle === null) return;
    let alive = true;
    fetch(staticFile(scene.routes))
      .then((r) => r.json())
      .then(async (json: unknown) => {
        const raw = json as {meta?: {family?: unknown}};
        const parsed = raw?.meta?.family === "free-random-touch"
          ? RandomTouchRoutesDataSchema.parse(json)
          : RoutesDataSchema.parse(json); // 로드 시점 검증 — 침묵 실패 금지

        // SVG <image>는 HTML <img>와 달리 Remotion의 delayRender를 자동으로 잡지 않는다.
        // 고해상도 RGBA가 decode/paint 되기 전에 병렬 Chromium이 캡처하면 종이 배경만
        // 1~수 프레임 찍히므로, routes handle을 풀기 전에 명시적으로 decode를 완료한다.
        const sources = [parsed.meta.image, scene.prewashImage]
          .filter((src): src is string => Boolean(src));
        await Promise.all(sources.map(async (src) => {
          const decoded = new Image();
          decoded.decoding = "sync";
          decoded.src = staticFile(src);
          await decoded.decode();
        }));

        if (alive) {
          parsed.strokes.sort((a, b) => a.start - b.start);
          setData(parsed);
        }
        continueRender(handle);
      })
      .catch((e: unknown) => {
        if (alive) setLoadError(String(e));
        continueRender(handle);
      });
    return () => {
      alive = false;
    };
  }, [scene.routes, scene.id, scene.prewashImage, handle]);

  // prewash 적용 중에는 붓 드로잉 타임라인을 지연 — 워시가 사라진 뒤 빈 종이에서 다시 그린다
  const introDelayFrames =
    scene.prewashOpacity > 0.001 && scene.prewashFrames > 0 ? Math.max(0, Math.round(scene.prewashFrames)) : 0;
  const drawFrame = frame - introDelayFrames;

  const randomData = data?.meta && "family" in data.meta && data.meta.family === "free-random-touch"
    ? data as RandomTouchRoutesData
    : null;
  const standardData = data && !randomData ? data as RoutesData : null;

  const dynamic = useMemo(
    () => (standardData ? buildDynamicStrokes(standardData.strokes, standardData.meta.penInvisibleAfter, scene.brushDynamics) : null),
    [standardData, scene.brushDynamics],
  );

  const revealTiming = standardData && dynamic
    ? getRevealTiming({
        scene,
        strokes: dynamic.strokes,
        penInvisibleAfter: dynamic.penInvisibleAfter,
        routesDuration: standardData.meta.durationInFrames,
      })
    : null;

  // 누적 드로잉/풀블리드 동화는 원본 프레임 자체가 연출의 진실이다.
  // route를 새로 생성해 원본 진행이나 캐릭터 외형을 훼손하지 않는다.
  if (scene.presentation && scene.image) {
    return (
      <AbsoluteFill style={{ backgroundColor: paper, overflow: "hidden" }}>
        <FullBleedPresentationLayer
          presentation={scene.presentation}
          image={scene.image}
          previousImage={scene.previousImage}
          durationInFrames={scene.durationInFrames}
        />
        <WidgetLayer frame={frame} widgets={scene.widgets} />
        {scene.topTitle && <TitleLayer frame={frame} spec={scene.topTitle} />}
        {scene.captionsVisible && <SubtitleLayer frame={frame} cues={scene.cues} style={scene.subtitleStyle} />}
      </AbsoluteFill>
    );
  }

  if ((!scene.routes && !scene.drawingPhases) || loadError) {
    return (
      <AbsoluteFill style={{ backgroundColor: paper, alignItems: "center", justifyContent: "center" }}>
        <div style={{ fontFamily: "monospace", fontSize: 28, color: "#b45a4a" }}>
          {loadError ? `routes 로드 실패: ${scene.routes} — ${loadError}` : `routes 누락: scene "${scene.id}"`}
        </div>
      </AbsoluteFill>
    );
  }

  // 첫 장면은 완성 이미지를 0.3초 동안 연하게 보여 준 뒤 종이로 지운다.
  // 이후부터 펜 외곽선 → 브러시 채색이 시작되며, 포스터는 첫 장면에만 허용한다.
  const useThumbnailPoster = scene.id === "scene-01";

  return (
    <AbsoluteFill style={{ backgroundColor: randomData ? "#01020d" : paper, overflow: "hidden" }}>
      {!randomData && <AbsoluteFill style={{ backgroundImage: PAPER_TEXTURE }} />}
      {scene.drawingPhases && (
        <AbsoluteFill style={{
          transform: `scale(${scene.viewportScale})`, transformOrigin: "center center",
          pointerEvents: "none",
        }}>
          <DrawingPhaseLayer
            sceneId={scene.id}
            phases={scene.drawingPhases}
            frame={frame}
            W={W}
            H={H}
            fallbackBrush={brush}
            outroFadeFrames={scene.outroFadeFrames}
            outroWashOpacity={scene.outroWashOpacity}
            outroBlur={scene.outroBlur}
            thumbnailPoster={useThumbnailPoster}
          />
        </AbsoluteFill>
      )}
      {randomData && <CosmicRandomBrushLayer data={randomData} frame={frame} W={W} H={H} />}
      {standardData && dynamic && (
        <>
          <RevealLayer
            scene={scene}
            image={standardData.meta.image}
            strokes={dynamic.strokes}
            penInvisibleAfter={dynamic.penInvisibleAfter}
            routesDuration={standardData.meta.durationInFrames}
            frame={frame}
            drawFrame={drawFrame}
            W={W}
            H={H}
            preserveSourceColor={Boolean((standardData.meta as Record<string, unknown>).preserveSourceColor)}
          />
          {scene.naturalEffects && revealTiming && (
            <EffectLayer
              frame={frame}
              drawFrame={drawFrame}
              startFrame={revealTiming.colorSettleEnd + 8}
              routesDuration={standardData.meta.durationInFrames}
              W={W}
              H={H}
              spec={scene.naturalEffects}
            />
          )}
          <CursorLayer
            frame={frame}
            drawFrame={drawFrame}
            strokes={dynamic.strokes}
            penInvisibleAfter={dynamic.penInvisibleAfter}
            linearDraw={scene.linearDraw}
            brush={brush}
            W={W}
            H={H}
          />
        </>
      )}
      <WidgetLayer frame={frame} widgets={scene.widgets} />
      {scene.topTitle && <TitleLayer frame={frame} spec={scene.topTitle} />}
      {scene.captionsVisible && <SubtitleLayer frame={frame} cues={scene.cues} style={scene.subtitleStyle} />}
    </AbsoluteFill>
  );
};

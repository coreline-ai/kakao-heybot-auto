// 컴포지션 등록부 — 등록 외의 로직을 두지 않는다.
import React from "react";
import { type CalculateMetadataFunction, Composition, useCurrentFrame } from "remotion";
import { FPS, FullColorMotionPropsSchema, RenderPropsSchema, type FullColorMotionProps, type RenderProps } from "./schema";
import { GALLERY_PAGES, PromoWidgetGallery } from "./promo/PromoWidgetGallery";
import { OrcaTriplePane } from "./promo/bespoke/OrcaTriplePane";
import { PromoSceneSequence } from "./promo/scene/PromoSceneSequence";
import { totalDuration } from "./promo/scene/transitions";
import { PAGE_FRAMES, PromoGalleryPropsSchema, PromoScenePropsSchema, type PromoGalleryProps, type PromoSceneProps } from "./promo/schema";
import { FullColorMotionSequence } from "./scene/FullColorMotionSequence";
import { SceneSequence } from "./scene/SceneSequence";

// duration = scenes 합산. props는 여기서 parse되어 스키마 기본값이 채워진 채 컴포넌트로 전달된다.
const calculateMetadata: CalculateMetadataFunction<RenderProps> = ({ props }) => {
  const parsed = RenderPropsSchema.parse(props);
  return {
    props: parsed,
    durationInFrames: parsed.scenes.reduce((sum, s) => sum + s.durationInFrames, 0),
    fps: FPS,
  };
};

const defaultProps: RenderProps = RenderPropsSchema.parse({
  schemaVersion: 1,
  projectId: "placeholder",
  scenes: [{ id: "scene-01", durationInFrames: 90 }],
});

const motionDefaultProps: FullColorMotionProps = FullColorMotionPropsSchema.parse({
  schemaVersion: 1,
  projectId: "motion-placeholder",
  scenes: [
    {
      id: "scene-01",
      image: "images/scene-01.png",
      durationInFrames: 300,
      movement: "push-in",
      effect: "rays",
    },
  ],
});

const promoGalleryDefaultProps: PromoGalleryProps = PromoGalleryPropsSchema.parse({
  pages: GALLERY_PAGES,
});

// duration = 페이지 수 × PAGE_FRAMES
const calculatePromoGalleryMetadata: CalculateMetadataFunction<PromoGalleryProps> = ({ props }) => {
  const parsed = PromoGalleryPropsSchema.parse(props);
  return { props: parsed, durationInFrames: parsed.pages.length * PAGE_FRAMES, fps: FPS };
};

const promoSceneDefaultProps: PromoSceneProps = PromoScenePropsSchema.parse({
  kicker: "PROMO SCENE // DEMO",
  scenes: [
    {
      durationInFrames: 90,
      subtitle: "씬 1 — 무대·전환 데모",
      stage: { preset: "orb", tint: "blue" },
      transition: { type: "none" },
      widgets: [{ type: "countUp", x: 560, y: 380, w: 800, h: 300, enterAt: 10, to: 31, decimals: 0, suffix: " WIDGETS", rule: true, caption: "DIRECTION LAYER" }],
      flashAt: [26],
    },
    {
      durationInFrames: 90,
      subtitle: "씬 2 — light-sweep 진입",
      stage: { preset: "grid", tint: "blue" },
      transition: { type: "light-sweep", durationInFrames: 12 },
      widgets: [{ type: "gauge", x: 660, y: 300, w: 600, h: 420, enterAt: 6, kind: "fill-arc", value: 100, max: 100, unit: "%", goldTail: true, caption: "STAGE" }],
    },
  ],
});

// duration = 씬 길이 합산 (가변)
const calculatePromoSceneMetadata: CalculateMetadataFunction<PromoSceneProps> = ({ props }) => {
  const parsed = PromoScenePropsSchema.parse(props);
  return { props: parsed, durationInFrames: totalDuration(parsed.scenes), fps: FPS };
};

// 베스포크 씬 POC 래퍼 — useCurrentFrame을 컴포지션 경계 안에서 주입
const BespokePOC: React.FC = () => {
  const frame = useCurrentFrame();
  return <OrcaTriplePane frame={frame} />;
};

const calculateMotionMetadata: CalculateMetadataFunction<FullColorMotionProps> = ({ props }) => {
  const parsed = FullColorMotionPropsSchema.parse(props);
  return {
    props: parsed,
    durationInFrames: parsed.scenes.reduce((sum, scene) => sum + scene.durationInFrames, 0),
    fps: FPS,
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="BrushLandscape"
        component={SceneSequence}
        width={1920}
        height={1080}
        fps={FPS}
        durationInFrames={90}
        schema={RenderPropsSchema}
        defaultProps={defaultProps}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="BrushLandscape4K"
        component={SceneSequence}
        width={3840}
        height={2160}
        fps={FPS}
        durationInFrames={90}
        schema={RenderPropsSchema}
        defaultProps={defaultProps}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="BrushPortrait"
        component={SceneSequence}
        width={1080}
        height={1920}
        fps={FPS}
        durationInFrames={90}
        schema={RenderPropsSchema}
        defaultProps={defaultProps}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="FullColorMotionLandscape"
        component={FullColorMotionSequence}
        width={1920}
        height={1080}
        fps={FPS}
        durationInFrames={300}
        schema={FullColorMotionPropsSchema}
        defaultProps={motionDefaultProps}
        calculateMetadata={calculateMotionMetadata}
      />
      <Composition
        id="FullColorMotionPortrait"
        component={FullColorMotionSequence}
        width={1080}
        height={1920}
        fps={FPS}
        durationInFrames={300}
        schema={FullColorMotionPropsSchema}
        defaultProps={motionDefaultProps}
        calculateMetadata={calculateMotionMetadata}
      />
      <Composition
        id="PromoWidgetGallery"
        component={PromoWidgetGallery}
        width={1920}
        height={1080}
        fps={FPS}
        durationInFrames={150}
        schema={PromoGalleryPropsSchema}
        defaultProps={promoGalleryDefaultProps}
        calculateMetadata={calculatePromoGalleryMetadata}
      />
      <Composition
        id="OrcaBespokePOC"
        component={BespokePOC}
        width={1920}
        height={1080}
        fps={FPS}
        durationInFrames={600}
      />
      <Composition
        id="PromoScene"
        component={PromoSceneSequence}
        width={1920}
        height={1080}
        fps={FPS}
        durationInFrames={180}
        schema={PromoScenePropsSchema}
        defaultProps={promoSceneDefaultProps}
        calculateMetadata={calculatePromoSceneMetadata}
      />
      <Composition
        id="FullColorMotionLandscape4K"
        component={FullColorMotionSequence}
        width={3840}
        height={2160}
        fps={FPS}
        durationInFrames={300}
        schema={FullColorMotionPropsSchema}
        defaultProps={motionDefaultProps}
        calculateMetadata={calculateMotionMetadata}
      />
    </>
  );
};

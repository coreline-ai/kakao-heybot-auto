import React from "react";
import {BrushScene} from "../scene/BrushScene";
import {SceneSchema} from "../schema";

// 승인 데모 호환 래퍼. 랜덤 터치 렌더링은 src/scene의 본선 레이어만 사용한다.
const scene = SceneSchema.parse({
  id: "cosmic-random-brush-demo",
  routes: "cosmic-dark-pilot/random-brush-routes.json",
  durationInFrames: 300,
  faint: 1,
  edgeFeather: 0,
  linearDraw: true,
  completionMode: "masked-hold",
  prewashOpacity: 0,
  prewashFrames: 0,
  prewashHoldFrames: 0,
  topTitle: {
    kicker: "FREE BRUSH STUDY",
    lines: ["03. 궤도 위 일출"],
    x: 68,
    y: 54,
    width: 760,
    enterAt: 7,
    accent: "#86e8ff",
    color: "#eef8ff",
    fontSize: 36,
    kickerFontSize: 14,
  },
});

export const CosmicRandomBrushDemo: React.FC = () =>
  <BrushScene scene={scene} paper="#01020d" />;

// 붓 커서: 지금 그려지고 있는 스트로크의 선두를 따라간다.
// 선을 실제로 드러내는 순간에만 보이고(이동/퇴장 모션 없음), brush 이미지가 없으면 커서를 그리지 않는다.
import React from "react";
import { staticFile } from "remotion";
import { sharedProgress } from "../lib/easing";
import { pointOnPolyline, type Point } from "../lib/geometry";
import type { Brush, Stroke } from "../schema";

export type PenPose = { x: number; y: number; angle: number };

const DEFAULT_PEN_SRC = "brush-draw/pen.svg";
const DEFAULT_PEN_VIEWBOX = { width: 360, height: 160, tipX: 8, tipY: 80 } as const;
const DEFAULT_PEN_ROTATION = -132;

export type PenSpriteLayout = {
  src: string;
  width: number;
  height: number;
  tipx: number;
  tipy: number;
  rotationOffset: number;
};

// kind: pen의 단일 소스는 public/brush-draw/pen.svg다.
// src/h/tip을 주면 프로젝트별 펜 에셋으로 교체할 수 있고, 생략하면 기본 SVG의 viewBox 비율을 사용한다.
export function getPenSpriteLayout(brush: Brush): PenSpriteLayout {
  const width = brush.w ?? 140;
  const height = brush.h ?? width * DEFAULT_PEN_VIEWBOX.height / DEFAULT_PEN_VIEWBOX.width;
  const usingDefaultAsset = brush.src == null;
  return {
    src: brush.src ?? DEFAULT_PEN_SRC,
    width,
    height,
    tipx: brush.tipx ?? (usingDefaultAsset ? width * DEFAULT_PEN_VIEWBOX.tipX / DEFAULT_PEN_VIEWBOX.width : 0),
    tipy: brush.tipy ?? (usingDefaultAsset ? height * DEFAULT_PEN_VIEWBOX.tipY / DEFAULT_PEN_VIEWBOX.height : height / 2),
    rotationOffset: DEFAULT_PEN_ROTATION,
  };
}

export function getPenPose(drawFrame: number, strokes: Stroke[], penOff: number, linear = false): PenPose | null {
  if (!strokes.length || drawFrame >= penOff) return null;
  for (const s of strokes) {
    if (drawFrame >= s.start && drawFrame <= s.end) {
      const p = sharedProgress(drawFrame, s.start, s.end, linear);
      return pointOnPolyline(s.points as Point[], p);
    }
  }
  return null;
}

type Props = {
  frame: number; // 절대 프레임 — 미세 흔들림(wobble)용
  drawFrame: number;
  strokes: Stroke[];
  penInvisibleAfter: number;
  linearDraw: boolean;
  brush?: Brush;
  W: number;
  H: number;
};

export const CursorLayer: React.FC<Props> = ({ frame, drawFrame, strokes, penInvisibleAfter, linearDraw, brush, W, H }) => {
  if (!brush || brush.visible === false || brush.opacity === 0) return null;
  const pose = getPenPose(drawFrame, strokes, penInvisibleAfter, linearDraw);
  if (!pose) return null;

  const layerStyle: React.CSSProperties = {
    position: "absolute",
    inset: 0,
    zIndex: 30,
    pointerEvents: "none",
    opacity: brush.opacity,
    filter: "drop-shadow(0 10px 14px rgba(0,0,0,0.12))",
  };

  // 펜 이미지 커서 — 실제 public 에셋을 사용해 코드와 시각 원본이 어긋나지 않게 한다.
  if (brush.kind === "pen") {
    const pen = getPenSpriteLayout(brush);
    const rot = Math.max(-34, Math.min(30, pose.angle * 0.18 - 6)) + Math.sin(frame * 0.42) * 0.7;
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={layerStyle}>
        <g transform={`translate(${pose.x} ${pose.y}) rotate(${rot + pen.rotationOffset})`}>
          <image
            href={staticFile(pen.src)}
            x={-pen.tipx}
            y={-pen.tipy}
            width={pen.width}
            height={pen.height}
            preserveAspectRatio="xMinYMid meet"
          />
        </g>
      </svg>
    );
  }

  // 이미지 커서(붓 등): 붓끝(tipx,tipy)을 그리는 지점에 앵커, +180도 회전 보정.
  const rot = Math.max(-52, Math.min(44, pose.angle * 0.24 - 8)) + Math.sin(frame * 0.42) * 0.9;
  if (brush.src == null) return null; // 스키마 refine이 보증하지만 안전망

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={layerStyle}>
      <g transform={`translate(${pose.x} ${pose.y}) rotate(${rot + 180})`}>
        <image
          href={staticFile(brush.src)}
          x={-(brush.tipx ?? 0)}
          y={-(brush.tipy ?? 0)}
          width={brush.w}
          height={brush.h}
          preserveAspectRatio="xMinYMin meet"
        />
      </g>
    </svg>
  );
};

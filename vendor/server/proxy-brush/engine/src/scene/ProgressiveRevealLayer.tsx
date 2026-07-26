import React, { useId } from "react";
import { staticFile } from "remotion";
import { sharedProgress } from "../lib/easing";
import { toPath, type Point } from "../lib/geometry";
import type { Stroke } from "../schema";

type Props = {
  image: string;
  strokes: Stroke[];
  frame: number;
  W: number;
  H: number;
  zIndex: number;
  opacity?: number;
  edgeFeather?: number;
};

/** routes가 지나간 자리만 RGBA 레이어를 노출한다. 완성본 강제 패치가 없는 순수 리빌 레이어. */
export const ProgressiveRevealLayer: React.FC<Props> = ({
  image, strokes, frame, W, H, zIndex, opacity = 1, edgeFeather = 0,
}) => {
  const id = `progressive-${useId().replace(/[:]/g, "")}`;
  const paths = strokes.map((stroke) => (
    <path
      key={stroke.id}
      d={toPath(stroke.points as Point[])}
      fill="none"
      stroke="#fff"
      strokeWidth={stroke.width}
      strokeLinecap="round"
      strokeLinejoin="round"
      pathLength={1}
      strokeDasharray={1}
      strokeDashoffset={1 - sharedProgress(frame, stroke.start, stroke.end, true)}
      opacity={frame < stroke.start ? 0 : 1}
    />
  ));

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
      style={{ position: "absolute", inset: 0, zIndex, pointerEvents: "none", opacity }}>
      <defs>
        {edgeFeather > 0 && <filter id={`${id}-blur`} x="-8%" y="-8%" width="116%" height="116%">
          <feGaussianBlur stdDeviation={edgeFeather} />
        </filter>}
        <mask id={`${id}-mask`} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
          <g filter={edgeFeather > 0 ? `url(#${id}-blur)` : undefined}>{paths}</g>
        </mask>
      </defs>
      <image href={staticFile(image)} x="0" y="0" width={W} height={H}
        preserveAspectRatio="none" transform="matrix(1 0 0 1 0 0)" mask={`url(#${id}-mask)`} />
    </svg>
  );
};

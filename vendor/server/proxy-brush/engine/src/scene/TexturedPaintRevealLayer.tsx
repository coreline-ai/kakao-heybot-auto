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

type Bristle = { offset: number; widthRatio: number; opacity: number };

const BRISTLES: Bristle[] = [
  { offset: -0.43, widthRatio: 0.16, opacity: 0.86 },
  { offset: -0.29, widthRatio: 0.18, opacity: 0.96 },
  { offset: -0.14, widthRatio: 0.15, opacity: 0.82 },
  { offset: 0.00, widthRatio: 0.19, opacity: 1.00 },
  { offset: 0.15, widthRatio: 0.14, opacity: 0.80 },
  { offset: 0.29, widthRatio: 0.18, opacity: 0.94 },
  { offset: 0.43, widthRatio: 0.15, opacity: 0.84 },
];

const clamp = (value: number, lower: number, upper: number) =>
  Math.max(lower, Math.min(upper, value));

/**
 * Shifts a route perpendicular to its local tangent. This keeps the pigment
 * strands tied to the same route that drives the visible brush tip instead of
 * revealing a broad rectangular colour block behind an unrelated cursor.
 */
const offsetPolyline = (points: readonly Point[], offset: number): Point[] =>
  points.map((point, index) => {
    const previous = points[Math.max(0, index - 1)];
    const next = points[Math.min(points.length - 1, index + 1)];
    const dx = next[0] - previous[0];
    const dy = next[1] - previous[1];
    const length = Math.max(0.001, Math.hypot(dx, dy));
    return [point[0] - (dy / length) * offset, point[1] + (dx / length) * offset] as Point;
  });

const dash = (frame: number, stroke: Stroke) =>
  1 - sharedProgress(frame, stroke.start, stroke.end, true);

const settledOpacity = (frame: number, stroke: Stroke): number => {
  // Fresh pigment stays visibly bristled while the brush is moving. After the
  // bristles leave, it gently wets into a continuous colour field over 0.2 s.
  const settle = clamp((frame - stroke.end) / 7, 0, 1);
  return 0.12 + settle * 0.88;
};

/**
 * Watercolour-like colour deposition for the paint phase.
 *
 * The prior layer used one opaque wide mask per route, which looked like a
 * generic reveal. Here, a low-opacity wet wash, seven uneven bristle tracks,
 * and a short post-stroke settling pass all use the exact same route. Final
 * coverage remains deterministic because the settled mask becomes opaque
 * before the final QA capture frames.
 */
export const TexturedPaintRevealLayer: React.FC<Props> = ({
  image, strokes, frame, W, H, zIndex, opacity = 1, edgeFeather = 0,
}) => {
  const id = `paint-texture-${useId().replace(/[:]/g, "")}`;
  const feather = Math.max(0.6, edgeFeather * 0.42);

  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      style={{ position: "absolute", inset: 0, zIndex, pointerEvents: "none", opacity }}
    >
      <defs>
        <filter id={`${id}-wash`} x="-8%" y="-8%" width="116%" height="116%">
          <feGaussianBlur stdDeviation={feather} />
        </filter>
        <mask id={`${id}-mask`} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
          {/* A translucent wet edge appears first, never as a full-frame patch. */}
          <g filter={`url(#${id}-wash)`}>
            {strokes.map((stroke) => (
              <path
                key={`${stroke.id}-wash`}
                d={toPath(stroke.points as Point[])}
                fill="none"
                stroke="#fff"
                strokeWidth={stroke.width * 1.08}
                strokeLinecap="round"
                strokeLinejoin="round"
                pathLength={1}
                strokeDasharray={1}
                strokeDashoffset={dash(frame, stroke)}
                opacity={frame < stroke.start ? 0 : 0.14}
              />
            ))}
          </g>

          {/* Parallel, slightly uneven bristle deposits: this is what the
              active brush visibly lays down before the paint settles. */}
          {strokes.flatMap((stroke) => BRISTLES.map((bristle, index) => (
            <path
              key={`${stroke.id}-bristle-${index}`}
              d={toPath(offsetPolyline(stroke.points as Point[], bristle.offset * stroke.width))}
              fill="none"
              stroke="#fff"
              strokeWidth={Math.max(1, stroke.width * bristle.widthRatio)}
              strokeLinecap="round"
              strokeLinejoin="round"
              pathLength={1}
              strokeDasharray={1}
              strokeDashoffset={dash(frame, stroke)}
              opacity={frame < stroke.start ? 0 : bristle.opacity}
            />
          )))}

          {/* The same painted path slowly blends into a continuous fill only
              after the brush tail has passed. */}
          {strokes.map((stroke) => (
            <path
              key={`${stroke.id}-settle`}
              d={toPath(stroke.points as Point[])}
              fill="none"
              stroke="#fff"
              strokeWidth={stroke.width}
              strokeLinecap="round"
              strokeLinejoin="round"
              pathLength={1}
              strokeDasharray={1}
              strokeDashoffset={dash(frame, stroke)}
              opacity={frame < stroke.start ? 0 : settledOpacity(frame, stroke)}
            />
          ))}
        </mask>
      </defs>
      <image
        href={staticFile(image)}
        x="0"
        y="0"
        width={W}
        height={H}
        preserveAspectRatio="none"
        transform="matrix(1 0 0 1 0 0)"
        mask={`url(#${id}-mask)`}
      />
    </svg>
  );
};

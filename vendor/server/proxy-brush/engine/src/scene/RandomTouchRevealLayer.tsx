import React, {useId} from "react";
import {staticFile} from "remotion";
import type {RandomTouchRoutesData} from "../schema";

const segmentAngle = (a: [number, number, number], b: [number, number, number]) =>
  Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;

type Props = {data: RandomTouchRoutesData; frame: number; W: number; H: number};

export const RandomTouchRevealLayer: React.FC<Props> = ({data, frame, W, H}) => {
  const uid = useId().replace(/[:]/g, "");
  const maskId = `random-touch-mask-${uid}`;
  const roughId = `random-touch-rough-${uid}`;

  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none"}}>
    <defs>
      <filter id={roughId} x="-10%" y="-10%" width="120%" height="120%">
        <feTurbulence type="fractalNoise" baseFrequency="0.012 0.075" numOctaves="2" seed="71" result="noise" />
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="19" xChannelSelector="R" yChannelSelector="B" />
      </filter>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
        <rect width={W} height={H} fill="#000" />
        <g filter={`url(#${roughId})`}>
          {data.strokes.flatMap((stroke, si) => stroke.points.slice(1).flatMap((point, index) => {
            const pi = index + 1;
            const pointFrame = stroke.start + (stroke.end - stroke.start) * (pi / (stroke.points.length - 1));
            if (frame < pointFrame) return [];
            const p0 = stroke.points[pi - 1];
            const p1 = point;
            const pressure = (p0[2] + p1[2]) * 0.5;
            const width = stroke.width * pressure;
            const rad = segmentAngle(p0, p1) * Math.PI / 180;
            const nx = -Math.sin(rad);
            const ny = Math.cos(rad);
            const lanes = [-0.42, -0.26, -0.11, 0.08, 0.23, 0.39];
            return <g key={`${stroke.id}-${pi}`} opacity={stroke.opacity}>
              <line x1={p0[0]} y1={p0[1]} x2={p1[0]} y2={p1[1]}
                stroke="#fff" strokeWidth={width * 0.72} strokeLinecap="round" opacity="0.82" />
              {lanes.map((lane, li) => {
                const wobble = Math.sin((si + 2) * 5.71 + pi * 2.93 + li) * width * 0.035;
                const offset = lane * width + wobble;
                const laneWidth = width * (0.07 + ((si + pi + li) % 4) * 0.018);
                return <line key={li}
                  x1={p0[0] + nx * offset} y1={p0[1] + ny * offset}
                  x2={p1[0] + nx * offset} y2={p1[1] + ny * offset}
                  stroke="#fff" strokeWidth={laneWidth} strokeLinecap="round"
                  opacity={0.24 + (1 - stroke.dryness) * 0.34} />;
              })}
            </g>;
          }))}
        </g>
      </mask>
    </defs>
    <g mask={`url(#${maskId})`}>
      <image href={staticFile(data.meta.image)} width={W} height={H} preserveAspectRatio="none" />
    </g>
  </svg>;
};

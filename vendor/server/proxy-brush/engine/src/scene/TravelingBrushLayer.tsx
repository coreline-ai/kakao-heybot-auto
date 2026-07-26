import React from "react";
import type {RandomTouchStroke} from "../schema";

export type RandomBrushPose = {
  x: number;
  y: number;
  pressure: number;
  angle: number;
  touching: boolean;
};

const segmentAngle = (a: [number, number, number], b: [number, number, number]) =>
  Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;

export const sampleRandomTouchStroke = (stroke: RandomTouchStroke, frame: number): RandomBrushPose => {
  const raw = Math.max(0, Math.min(1, (frame - stroke.start) / Math.max(0.001, stroke.end - stroke.start)));
  const eased = raw * raw * (3 - 2 * raw);
  const cursor = eased * (stroke.points.length - 1);
  const i0 = Math.floor(cursor);
  const i1 = Math.min(stroke.points.length - 1, i0 + 1);
  const mix = cursor - i0;
  const a = stroke.points[i0];
  const b = stroke.points[i1];
  return {
    x: a[0] + (b[0] - a[0]) * mix,
    y: a[1] + (b[1] - a[1]) * mix,
    pressure: a[2] + (b[2] - a[2]) * mix,
    angle: segmentAngle(a, b),
    touching: true,
  };
};

export const randomTouchBrushPose = (frame: number, strokes: RandomTouchStroke[]): RandomBrushPose | null => {
  if (!strokes.length || frame < strokes[0].start - 5 || frame > strokes[strokes.length - 1].end + 5) return null;
  const active = strokes.find((stroke) => frame >= stroke.start && frame <= stroke.end);
  if (active) return sampleRandomTouchStroke(active, frame);

  const nextIndex = strokes.findIndex((stroke) => frame < stroke.start);
  if (nextIndex < 1) return null;
  const previous = strokes[nextIndex - 1];
  const next = strokes[nextIndex];
  const gap = Math.max(0.001, next.start - previous.end);
  const raw = Math.max(0, Math.min(1, (frame - previous.end) / gap));
  const eased = 0.5 - Math.cos(raw * Math.PI) * 0.5;
  const from = previous.points[previous.points.length - 1];
  const to = next.points[0];
  const arc = Math.sin(raw * Math.PI) * 34;
  return {
    x: from[0] + (to[0] - from[0]) * eased,
    y: from[1] + (to[1] - from[1]) * eased - arc,
    pressure: 0.08,
    angle: Math.atan2(to[1] - from[1], to[0] - from[0]) * 180 / Math.PI,
    touching: false,
  };
};

type Props = {frame: number; strokes: RandomTouchStroke[]; W: number; H: number};

export const TravelingBrushLayer: React.FC<Props> = ({frame, strokes, W, H}) => {
  const pose = randomTouchBrushPose(frame, strokes);
  if (!pose) return null;
  const scale = pose.touching ? 0.9 + pose.pressure * 0.13 : 0.88;
  const tilt = pose.touching ? 0 : -4;
  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 36, pointerEvents: "none"}}>
    <defs>
      <filter id="random-brush-shadow" x="-80%" y="-80%" width="260%" height="260%">
        <feGaussianBlur stdDeviation="7" />
      </filter>
      <linearGradient id="random-brush-handle" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stopColor="#05070c" />
        <stop offset="0.5" stopColor="#263246" />
        <stop offset="0.72" stopColor="#070910" />
        <stop offset="1" stopColor="#52657b" />
      </linearGradient>
      <linearGradient id="random-brush-metal" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="#dce9f5" />
        <stop offset="0.45" stopColor="#5d7188" />
        <stop offset="1" stopColor="#17202d" />
      </linearGradient>
    </defs>
    <g transform={`translate(${pose.x} ${pose.y}) rotate(${pose.angle + tilt}) scale(${scale})`}>
      <path d="M-18 0 L-322 -40" stroke="rgba(0,0,0,0.62)" strokeWidth="30" strokeLinecap="round"
        transform="translate(10 12)" filter="url(#random-brush-shadow)" />
      <path d="M-112 0 L-326 -42" stroke="url(#random-brush-handle)" strokeWidth="24" strokeLinecap="round" />
      <path d="M-106 -16 L-58 -12 L-44 13 L-108 16Z" fill="url(#random-brush-metal)" />
      <path d="M-49 -14 C-31 -17 -10 -11 9 0 C-11 12 -31 17 -50 14 C-43 5 -43 -5 -49 -14Z"
        fill={pose.touching ? "#c6e9f1" : "#8594a6"} />
      <path d="M-45 -8 C-23 -7 -7 -3 9 0" fill="none" stroke="#f6fbff" strokeWidth="3" opacity="0.72" />
      {pose.touching && <ellipse cx="6" cy="0" rx="27" ry="18" fill="#74e3ff" opacity="0.19"
        filter="url(#random-brush-shadow)" />}
    </g>
  </svg>;
};

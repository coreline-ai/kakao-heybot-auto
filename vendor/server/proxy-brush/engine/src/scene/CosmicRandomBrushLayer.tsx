import React from "react";
import {AbsoluteFill, Img, interpolate, staticFile} from "remotion";
import type {RandomTouchRoutesData} from "../schema";
import {RandomTouchRevealLayer} from "./RandomTouchRevealLayer";
import {TravelingBrushLayer} from "./TravelingBrushLayer";

const clamp = {extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const};
const CANVAS = "#01020d";

const Stars: React.FC<{frame: number; W: number; H: number}> = ({frame, W, H}) =>
  <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 3, pointerEvents: "none"}}>
    {Array.from({length: 26}, (_, i) => {
      const x = 38 + ((i * 277 + 83) % Math.round(W * 0.615));
      const y = 28 + ((i * 157 + 41) % Math.round(H * 0.667));
      const pulse = 0.2 + 0.46 * (0.5 + 0.5 * Math.sin(frame * (0.026 + (i % 4) * 0.006) + i));
      return <circle key={i} cx={x} cy={y} r={0.8 + (i % 4) * 0.35}
        fill={i % 6 === 0 ? "#ffd887" : "#ddf8ff"} opacity={pulse} />;
    })}
  </svg>;

type Props = {data: RandomTouchRoutesData; frame: number; W: number; H: number};

export const CosmicRandomBrushLayer: React.FC<Props> = ({data, frame, W, H}) => {
  const settle = interpolate(frame, [data.meta.settleStart, data.meta.settleEnd], [0, 1], clamp);
  const outro = interpolate(frame, [data.meta.durationInFrames - 25, data.meta.durationInFrames - 1], [0, 1], clamp);
  return <AbsoluteFill style={{backgroundColor: CANVAS, overflow: "hidden", zIndex: 1}}>
    <AbsoluteFill style={{backgroundImage:
      "radial-gradient(circle at 76% 52%, rgba(39,91,176,0.14), transparent 44%), radial-gradient(circle at 18% 14%, rgba(86,61,147,0.08), transparent 38%)"}} />
    <Stars frame={frame} W={W} H={H} />
    <RandomTouchRevealLayer data={data} frame={frame} W={W} H={H} />
    {/* Frame 0부터 마운트된 Remotion Img가 같은 source의 decode를 gate해 SVG mask의 병렬 빈 프레임을 막는다. */}
    <Img src={staticFile(data.meta.image)} style={{position: "absolute", inset: 0, zIndex: 12,
      opacity: settle, width: W, height: H, objectFit: "fill"}} />
    {frame <= data.meta.brushInvisibleAfter &&
      <TravelingBrushLayer frame={frame} strokes={data.strokes} W={W} H={H} />}
    <AbsoluteFill style={{zIndex: 90, backgroundColor: CANVAS, opacity: outro}} />
  </AbsoluteFill>;
};

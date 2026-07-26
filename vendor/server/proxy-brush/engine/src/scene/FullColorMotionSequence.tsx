// Full Color Motion — 원본 정지 이미지를 보존한 2D Ken-Burns 모션/효과 시퀀스.
// brush/pen 리빌과 다른 props 계약을 사용하지만, 선택형 brush reveal은 기존 routes JSON을
// mask/cursor 용도로만 재사용한다. 실제 3D camera·tracking·AI video를 가장하지 않는다.
import React, {useEffect, useId, useMemo, useState} from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  Sequence,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import {clamp, sharedProgress} from "../lib/easing";
import {toPath, type Point} from "../lib/geometry";
import {
  type Brush,
  type FullColorMotionProps,
  type FullColorMotionScene,
  type MotionMovement,
  type RoutesData,
  type Stroke,
  RoutesDataSchema,
} from "../schema";
import {CursorLayer} from "./CursorLayer";
import {SubtitleLayer} from "./SubtitleLayer";
import {TitleLayer} from "./TitleLayer";

export const FULL_COLOR_MOTION_TRANSITION_FRAMES = 12;

const clampProgress = (frame: number, duration: number) =>
  interpolate(frame, [0, Math.max(duration - 1, 1)], [0, 1], clamp);

export const cameraTransform = (movement: MotionMovement, progress: number) => {
  const eased = Easing.inOut(Easing.cubic)(progress);
  const gentleWave = Math.sin(progress * Math.PI) * 12;
  let scale = 1.03;
  let x = 0;
  let y = 0;

  switch (movement) {
    case "push-in":
      scale = 1.03 + eased * 0.12;
      break;
    case "push-out":
      scale = 1.15 - eased * 0.12;
      break;
    case "pan-left":
      scale = 1.1;
      x = 56 - eased * 112;
      y = gentleWave * 0.35;
      break;
    case "pan-right":
      scale = 1.1;
      x = -56 + eased * 112;
      y = -gentleWave * 0.35;
      break;
    case "rise":
      scale = 1.1;
      y = 42 - eased * 92;
      break;
    case "fall":
      scale = 1.1;
      y = -42 + eased * 92;
      break;
    case "arc-left":
      scale = 1.1;
      x = 46 - eased * 92;
      y = -28 + Math.sin(progress * Math.PI) * 54;
      break;
    case "arc-right":
      scale = 1.1;
      x = -46 + eased * 92;
      y = -28 + Math.sin(progress * Math.PI) * 54;
      break;
  }

  return `translate(${x}px, ${y}px) scale(${scale})`;
};

export const getFullColorMotionState = (
  frame: number,
  scene: Pick<FullColorMotionScene, "durationInFrames" | "movement" | "reveal">,
  crossfadeIn: boolean,
) => {
  const timelineDuration = scene.durationInFrames + (crossfadeIn ? FULL_COLOR_MOTION_TRANSITION_FRAMES : 0);
  const progress = clampProgress(frame, timelineDuration);
  const revealFrames = scene.reveal.mode === "brush" ? scene.reveal.frames : 0;
  return {
    progress,
    transform: cameraTransform(scene.movement, progress),
    incomingOpacity: crossfadeIn
      ? interpolate(frame, [0, FULL_COLOR_MOTION_TRANSITION_FRAMES], [0, 1], clamp)
      : 1,
    revealProgress: revealFrames > 0
      ? interpolate(frame, [0, revealFrames], [0, 1], clamp)
      : 1,
  };
};

const Rays: React.FC<{time: number; intensity: number}> = ({time, intensity}) => (
  <AbsoluteFill
    style={{
      opacity: 0.1 + Math.sin(time * 0.65) * 0.035 + intensity * 0.035,
      background:
        "conic-gradient(from 214deg at 34% 48%, transparent 0deg, rgba(255,223,151,0.42) 9deg, transparent 18deg, transparent 46deg, rgba(255,209,121,0.25) 57deg, transparent 68deg, transparent 105deg, rgba(255,234,179,0.3) 117deg, transparent 133deg)",
      mixBlendMode: "screen",
      transform: `scale(1.1) rotate(${time * 0.4}deg)`,
    }}
  />
);

const Mist: React.FC<{time: number; intensity: number}> = ({time, intensity}) => (
  <AbsoluteFill style={{opacity: 0.14 + intensity * 0.05, filter: "blur(28px)"}}>
    {[0, 1, 2, 3].map((i) => {
      const drift = ((time * (8 + i * 1.3) + i * 29) % 150) - 35;
      return (
        <div
          key={i}
          style={{
            position: "absolute", left: `${drift}%`, top: `${57 + i * 10}%`,
            width: `${72 + i * 14}%`, height: "25%", borderRadius: "50%",
            background: "rgba(227,231,255,0.42)",
          }}
        />
      );
    })}
  </AbsoluteFill>
);

const Birds: React.FC<{time: number}> = ({time}) => (
  <AbsoluteFill style={{opacity: 0.7}}>
    {[0, 1, 2, 3, 4].map((i) => {
      const x = ((time * (7 + i) + i * 21) % 125) - 12;
      const y = 20 + ((i * 13 + time * 1.5) % 19);
      const wing = 4 + Math.sin(time * 8 + i) * 2;
      return (
        <svg key={i} viewBox="0 0 32 16" style={{position: "absolute", width: 32 + i * 3, height: 16 + i, left: `${x}%`, top: `${y}%`, transform: `rotate(${Math.sin(time + i) * 8}deg)`}}>
          <path d={`M 1 10 Q 8 ${10 - wing} 16 10 Q 24 ${10 - wing} 31 10`} fill="none" stroke="rgba(31,23,48,0.78)" strokeWidth="2.2" strokeLinecap="round" />
        </svg>
      );
    })}
  </AbsoluteFill>
);

const Stars: React.FC<{time: number; warm?: boolean}> = ({time, warm = false}) => (
  <AbsoluteFill>
    {Array.from({length: 42}, (_, i) => {
      const left = (i * 37 + 11) % 100;
      const top = (i * 19 + 7) % 74;
      const pulse = 0.22 + ((Math.sin(time * (1.4 + (i % 5) * 0.25) + i) + 1) / 2) * 0.72;
      const size = 1.5 + (i % 4) * 0.7;
      return <div key={i} style={{position: "absolute", left: `${left}%`, top: `${top}%`, width: size, height: size, borderRadius: "50%", opacity: pulse, background: warm ? "#ffe6a1" : "#eff4ff", boxShadow: `0 0 ${size * 3}px ${warm ? "#ffbd56" : "#a6c8ff"}`}} />;
    })}
  </AbsoluteFill>
);

const Storm: React.FC<{time: number}> = ({time}) => {
  const lightning = Math.max(0, Math.sin(time * 2.1 - 0.7)) ** 28;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{opacity: 0.2 + Math.sin(time * 0.6) * 0.05, background: "linear-gradient(135deg, rgba(19,14,53,0.52), transparent 58%, rgba(89,41,125,0.22))", mixBlendMode: "multiply"}} />
      <AbsoluteFill style={{opacity: lightning * 0.23, background: "linear-gradient(120deg, transparent 45%, rgba(229,223,255,0.74) 49%, transparent 54%)", mixBlendMode: "screen"}} />
    </AbsoluteFill>
  );
};

const RisingLights: React.FC<{time: number; color: string; count: number; size: number}> = ({time, color, count, size}) => (
  <AbsoluteFill>
    {Array.from({length: count}, (_, i) => {
      const left = (i * 29 + 6) % 96;
      const travel = (time * (3.4 + (i % 4) * 0.55) + i * 7) % 112;
      const top = 110 - travel;
      const wobble = Math.sin(time * 1.7 + i) * 1.4;
      const dotSize = size + (i % 3) * 1.4;
      return <div key={i} style={{position: "absolute", left: `${left + wobble}%`, top: `${top}%`, width: dotSize, height: dotSize * 1.35, borderRadius: "50% 50% 45% 45%", opacity: 0.38 + (i % 4) * 0.11, background: color, boxShadow: `0 0 ${dotSize * 4}px ${color}`}} />;
    })}
  </AbsoluteFill>
);

const Ripples: React.FC<{time: number}> = ({time}) => (
  <AbsoluteFill style={{overflow: "hidden", opacity: 0.43}}>
    {[0, 1, 2].map((i) => {
      const phase = (time * 0.18 + i / 3) % 1;
      return <div key={i} style={{position: "absolute", left: "50%", top: "78%", width: `${16 + phase * 92}%`, height: `${4 + phase * 20}%`, border: "2px solid rgba(224,239,255,0.62)", borderRadius: "50%", transform: "translate(-50%, -50%)", opacity: 1 - phase}} />;
    })}
  </AbsoluteFill>
);

const Aurora: React.FC<{time: number}> = ({time}) => (
  <AbsoluteFill style={{overflow: "hidden", opacity: 0.33, filter: "blur(22px)", mixBlendMode: "screen"}}>
    {[0, 1, 2].map((i) => <div key={i} style={{position: "absolute", left: `${-22 + i * 22 + Math.sin(time * 0.45 + i) * 5}%`, top: `${6 + i * 11}%`, width: "72%", height: "27%", borderRadius: "50%", transform: `rotate(${-16 + i * 18 + Math.sin(time * 0.3 + i) * 5}deg)`, background: i === 1 ? "linear-gradient(90deg, transparent, rgba(131,255,207,0.65), transparent)" : "linear-gradient(90deg, transparent, rgba(158,118,255,0.58), transparent)"}} />)}
  </AbsoluteFill>
);

const Trail: React.FC<{time: number}> = ({time}) => (
  <AbsoluteFill style={{overflow: "hidden", opacity: 0.55, mixBlendMode: "screen"}}>
    {[0, 1, 2].map((i) => <div key={i} style={{position: "absolute", left: `${-42 + ((time * (12 + i * 2.4) + i * 38) % 170)}%`, top: `${22 + i * 18}%`, width: "28%", height: 4 + i * 2, borderRadius: 999, transform: "rotate(-20deg)", background: "linear-gradient(90deg, transparent, rgba(255,224,151,0.88), transparent)", boxShadow: "0 0 18px rgba(255,207,101,0.7)"}} />)}
  </AbsoluteFill>
);

const MotionEffectLayer: React.FC<{effect: FullColorMotionScene["effect"]; time: number; intensity: number}> = ({effect, time, intensity}) => {
  switch (effect) {
    case "rays": return <Rays time={time} intensity={intensity} />;
    case "mist": return <Mist time={time} intensity={intensity} />;
    case "birds": return <Birds time={time} />;
    case "stars": return <Stars time={time} />;
    case "storm": return <Storm time={time} />;
    case "sparkles": return <RisingLights time={time} color="#ffe29a" count={28} size={3} />;
    case "lanterns": return <RisingLights time={time} color="#ffb34f" count={19} size={7} />;
    case "fireflies": return <RisingLights time={time} color="#e8ff8e" count={25} size={2.5} />;
    case "ripples": return <Ripples time={time} />;
    case "aurora": return <Aurora time={time} />;
    case "trail": return <Trail time={time} />;
    case "none": return null;
  }
};

const rescaleStrokes = (strokes: Stroke[], sourceEnd: number, targetEnd: number): Stroke[] => {
  const ratio = targetEnd / Math.max(1, sourceEnd);
  return strokes.map((stroke) => ({...stroke, start: stroke.start * ratio, end: stroke.end * ratio}));
};

const MotionBrushRevealLayer: React.FC<{
  scene: FullColorMotionScene;
  frame: number;
  imageTransform: string;
  brush?: Brush;
  width: number;
  height: number;
}> = ({scene, frame, imageTransform, brush, width, height}) => {
  const routePath = scene.reveal.routes;
  const [data, setData] = useState<RoutesData | null>(null);
  const [failed, setFailed] = useState(false);
  const [handle] = useState(() => routePath ? delayRender(`motion-routes:${scene.id}`) : null);
  const maskId = `motion-brush-${useId().replace(/[:]/g, "")}`;

  useEffect(() => {
    if (!routePath || handle === null) return;
    let alive = true;
    fetch(staticFile(routePath))
      .then((response) => response.json())
      .then(async (raw: unknown) => {
        const parsed = RoutesDataSchema.parse(raw);
        const decoded = new Image();
        decoded.decoding = "sync";
        decoded.src = staticFile(scene.image);
        await decoded.decode();
        if (alive) setData(parsed);
        continueRender(handle);
      })
      .catch(() => {
        if (alive) setFailed(true);
        continueRender(handle);
      });
    return () => { alive = false; };
  }, [handle, routePath, scene.id, scene.image]);

  const sourceEnd = data?.strokes.reduce((max, stroke) => Math.max(max, stroke.end), data?.meta.drawEnd ?? 1) ?? 1;
  const strokes = useMemo(
    () => data ? rescaleStrokes(data.strokes, sourceEnd, scene.reveal.frames) : [],
    [data, sourceEnd, scene.reveal.frames],
  );
  const globalFill = interpolate(frame, [Math.round(scene.reveal.frames * 0.72), scene.reveal.frames], [0, 1], clamp);

  if (failed || !data) return null;
  return (
    <>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{position: "absolute", inset: 0, zIndex: 8, transform: imageTransform, transformOrigin: "center center"}}>
        <defs>
          <pattern id={`${maskId}-pattern`} patternUnits="userSpaceOnUse" width={width} height={height}>
            <image href={staticFile(scene.image)} x="0" y="0" width={width} height={height} preserveAspectRatio="none" />
          </pattern>
          <mask id={`${maskId}-mask`} maskUnits="userSpaceOnUse" x="0" y="0" width={width} height={height}>
            <g>{strokes.map((stroke) => <path key={stroke.id} d={toPath(stroke.points as Point[])} fill="none" stroke="#fff" strokeWidth={stroke.width} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - sharedProgress(frame, stroke.start, stroke.end, false)} />)}</g>
            <rect x="0" y="0" width={width} height={height} fill="#fff" opacity={globalFill} />
          </mask>
        </defs>
        <rect width={width} height={height} fill={`url(#${maskId}-pattern)`} mask={`url(#${maskId}-mask)`} />
      </svg>
      <div style={{position: "absolute", inset: 0, zIndex: 30, transform: imageTransform, transformOrigin: "center center"}}>
        <CursorLayer frame={frame} drawFrame={frame} strokes={strokes} penInvisibleAfter={scene.reveal.frames} linearDraw={false} brush={scene.reveal.cursor ?? brush} W={width} H={height} />
      </div>
    </>
  );
};

const FullColorMotionSceneLayer: React.FC<{scene: FullColorMotionScene; crossfadeIn: boolean; brush?: Brush}> = ({scene, crossfadeIn, brush}) => {
  const frame = useCurrentFrame();
  const {width, height, fps} = useVideoConfig();
  const state = getFullColorMotionState(frame, scene, crossfadeIn);
  const isBrushReveal = scene.reveal.mode === "brush" && frame < scene.reveal.frames;
  const time = frame / fps;
  const imageStyle: React.CSSProperties = {
    width: "100%", height: "100%", objectFit: "cover", transform: state.transform,
    transformOrigin: "center center", willChange: "transform",
    opacity: isBrushReveal ? Math.max(0, (state.revealProgress - 0.72) / 0.28) : 1,
  };

  return (
    <AbsoluteFill style={{overflow: "hidden", backgroundColor: "#090617", opacity: state.incomingOpacity}}>
      <Img src={staticFile(scene.image)} style={imageStyle} />
      {isBrushReveal && <MotionBrushRevealLayer scene={scene} frame={frame} imageTransform={state.transform} brush={brush} width={width} height={height} />}
      <MotionEffectLayer effect={scene.effect} time={time} intensity={scene.intensity} />
      <AbsoluteFill style={{pointerEvents: "none", background: "radial-gradient(ellipse at center, transparent 47%, rgba(8,4,22,0.05) 72%, rgba(8,4,22,0.28) 120%)"}} />
      {scene.topTitle && <TitleLayer frame={frame} spec={scene.topTitle} />}
      {scene.captionsVisible && <SubtitleLayer frame={frame} cues={scene.cues} style={scene.subtitleStyle} />}
    </AbsoluteFill>
  );
};

export const FullColorMotionSequence: React.FC<FullColorMotionProps> = ({scenes, brush}) => {
  let offset = 0;
  return (
    <AbsoluteFill style={{backgroundColor: "#090617"}}>
      {scenes.map((scene, index) => {
        const crossfadeIn = index > 0;
        const from = crossfadeIn ? offset - FULL_COLOR_MOTION_TRANSITION_FRAMES : offset;
        const durationInFrames = scene.durationInFrames + (crossfadeIn ? FULL_COLOR_MOTION_TRANSITION_FRAMES : 0);
        offset += scene.durationInFrames;
        return <Sequence key={scene.id} from={from} durationInFrames={durationInFrames} premountFor={30}>
          <FullColorMotionSceneLayer scene={scene} crossfadeIn={crossfadeIn} brush={brush} />
        </Sequence>;
      })}
    </AbsoluteFill>
  );
};

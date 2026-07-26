import React, {useEffect, useId, useState} from "react";
import {
  AbsoluteFill,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import {sharedProgress} from "../lib/easing";
import {toPath, type Point} from "../lib/geometry";
import {getPenPose} from "../scene/CursorLayer";
import {RoutesDataSchema, type RoutesData, type Stroke} from "../schema";

const W = 1920;
const H = 1080;
const CANVAS = "#01020d";

const clamp = {extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const};

const useRoutes = (src: string) => {
  const [routes, setRoutes] = useState<RoutesData | null>(null);
  const [handle] = useState(() => delayRender(`cosmic-routes:${src}`));
  useEffect(() => {
    let alive = true;
    fetch(staticFile(src))
      .then((res) => res.json())
      .then(async (raw: unknown) => {
        const parsed = RoutesDataSchema.parse(raw);
        const decoded = new Image();
        decoded.decoding = "sync";
        decoded.src = staticFile(parsed.meta.image);
        await decoded.decode();
        if (alive) setRoutes(parsed);
        continueRender(handle);
      })
      .catch((error) => {
        continueRender(handle);
        throw error;
      });
    return () => {
      alive = false;
    };
  }, [handle, src]);
  return routes;
};

const ProgressiveLuminousImage: React.FC<{image: string; strokes: Stroke[]}> = ({image, strokes}) => {
  const frame = useCurrentFrame();
  const uid = useId().replace(/[:]/g, "");
  const maskId = `cosmic-mask-${uid}`;
  const blurId = `cosmic-mask-blur-${uid}`;

  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none"}}>
    <defs>
      <filter id={blurId} x="-4%" y="-4%" width="108%" height="108%">
        <feGaussianBlur stdDeviation="2.4" />
      </filter>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
        <g filter={`url(#${blurId})`}>
          {strokes.map((stroke) => {
            const progress = sharedProgress(frame, stroke.start, stroke.end, true);
            return <path key={stroke.id} d={toPath(stroke.points as Point[])} fill="none"
              stroke="#fff" strokeWidth={stroke.width} strokeLinecap="round" strokeLinejoin="round"
              pathLength={1} strokeDasharray={1} strokeDashoffset={1 - progress}
              opacity={frame < stroke.start ? 0 : 1} />;
          })}
        </g>
      </mask>
    </defs>
    <g transform="translate(0 0)" mask={`url(#${maskId})`}>
      <image href={staticFile(image)} x={0} y={0} width={W} height={H} preserveAspectRatio="none" />
    </g>
  </svg>;
};

const CosmicStars: React.FC = () => {
  const frame = useCurrentFrame();
  const stars = Array.from({length: 34}, (_, i) => {
    const x = 45 + ((i * 263 + 71) % 1120);
    const y = 34 + ((i * 149 + 37) % 670);
    const r = 0.7 + (i % 5) * 0.32;
    const pulse = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(frame * (0.035 + (i % 4) * 0.008) + i * 1.7));
    return <circle key={i} cx={x} cy={y} r={r} fill={i % 7 === 0 ? "#ffd58a" : "#d9f6ff"}
      opacity={pulse * (i % 6 === 0 ? 0.7 : 0.38)} />;
  });
  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 3, pointerEvents: "none"}}>{stars}</svg>;
};

const LightBrushCursor: React.FC<{strokes: Stroke[]; penOff: number}> = ({strokes, penOff}) => {
  const frame = useCurrentFrame();
  const pose = getPenPose(frame, strokes, penOff, true);
  if (!pose) return null;
  const rotation = Math.max(-38, Math.min(32, pose.angle * 0.18 - 10));
  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 30, pointerEvents: "none"}}>
    <defs>
      <filter id="cosmic-tip-glow" x="-300%" y="-300%" width="600%" height="600%">
        <feGaussianBlur stdDeviation="13" />
      </filter>
      <linearGradient id="cosmic-brush-handle" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#edf9ff" />
        <stop offset="0.38" stopColor="#6fcfe9" />
        <stop offset="1" stopColor="#283760" />
      </linearGradient>
    </defs>
    <g transform={`translate(${pose.x} ${pose.y}) rotate(${rotation})`}>
      <circle cx="0" cy="0" r="32" fill="#77e8ff" opacity="0.42" filter="url(#cosmic-tip-glow)" />
      <path d="M-8 -14 L-112 -150" stroke="rgba(0,0,0,0.35)" strokeWidth="22" strokeLinecap="round"
        transform="translate(7 8)" />
      <path d="M-8 -14 L-112 -150" stroke="url(#cosmic-brush-handle)" strokeWidth="17" strokeLinecap="round" />
      <path d="M0 0 L-27 -7 L-8 -28 Z" fill="#ffe3a1" />
      <path d="M0 0 L-13 -4 L-4 -14 Z" fill="#ffffff" />
      <circle cx="0" cy="0" r="4.8" fill="#dffaff" />
    </g>
  </svg>;
};

const CopyLayer: React.FC = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [10, 28], [0, 1], clamp);
  const cueOpacity = Math.min(
    interpolate(frame, [198, 216], [0, 1], clamp),
    interpolate(frame, [258, 274], [1, 0], clamp),
  );
  return <AbsoluteFill style={{zIndex: 70, pointerEvents: "none", transform: "translateZ(0)"}}>
    <div style={{position: "absolute", left: 72, top: 58, opacity: titleOpacity,
      fontFamily: '-apple-system, "Apple SD Gothic Neo", sans-serif', color: "#f1f8ff"}}>
      <div style={{fontSize: 15, fontWeight: 850, letterSpacing: 4.4, color: "#8fe9ff"}}>DARK BRUSH PILOT</div>
      <div style={{fontSize: 38, fontWeight: 780, marginTop: 8, letterSpacing: -1.1,
        textShadow: "0 0 22px rgba(85,205,255,0.3)"}}>03. 궤도 위 일출</div>
    </div>
    <div style={{position: "absolute", left: 0, right: 0, bottom: 54, opacity: cueOpacity,
      display: "flex", justifyContent: "center", fontFamily: '-apple-system, "Apple SD Gothic Neo", sans-serif'}}>
      <div style={{padding: "13px 30px 14px", borderRadius: 999, color: "#eef8ff", fontSize: 31,
        fontWeight: 700, letterSpacing: -0.3, background: "rgba(2,7,20,0.58)",
        border: "1px solid rgba(143,233,255,0.2)", backdropFilter: "blur(5px)",
        boxShadow: "0 10px 34px rgba(0,0,0,0.28), 0 0 34px rgba(75,183,255,0.08)"}}>
        빛이 지구의 곡선을 천천히 깨운다
      </div>
    </div>
  </AbsoluteFill>;
};

export const CosmicDarkBrushDemo: React.FC = () => {
  const frame = useCurrentFrame();
  const routes = useRoutes("cosmic-dark-pilot/routes.json");
  const outro = interpolate(frame, [272, 299], [0, 1], clamp);
  const settle = interpolate(frame, [190, 236], [0, 1], clamp);

  return <AbsoluteFill style={{backgroundColor: CANVAS, overflow: "hidden"}}>
    <AbsoluteFill style={{backgroundImage:
      "radial-gradient(circle at 77% 55%, rgba(39,91,176,0.14), transparent 43%), radial-gradient(circle at 18% 14%, rgba(86,61,147,0.08), transparent 38%)"}} />
    <CosmicStars />
    {routes && <ProgressiveLuminousImage image={routes.meta.image} strokes={routes.strokes} />}
    {routes && <LightBrushCursor strokes={routes.strokes} penOff={routes.meta.penInvisibleAfter} />}
    <AbsoluteFill style={{zIndex: 21, pointerEvents: "none", opacity: settle * 0.07,
      background: "radial-gradient(circle at 78% 42%, rgba(255,193,87,0.5), transparent 24%), radial-gradient(circle at 56% 62%, rgba(64,185,255,0.25), transparent 46%)"}} />
    <CopyLayer />
    <AbsoluteFill style={{zIndex: 90, backgroundColor: CANVAS, opacity: outro, pointerEvents: "none"}} />
  </AbsoluteFill>;
};

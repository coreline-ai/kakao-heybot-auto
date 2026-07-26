import React, {useEffect, useId, useState} from "react";
import {
  AbsoluteFill,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";

const W = 1920;
const H = 1080;
const CANVAS = "#01020d";
const clamp = {extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const};

type SemanticPoint = [number, number, number];
type SemanticStroke = {
  id: string;
  group: "atmosphere" | "earth-surface" | "sunrise-rays" | "cloud-details";
  width: number;
  start: number;
  end: number;
  opacity: number;
  bristle: number;
  points: SemanticPoint[];
};
type SemanticRoutes = {
  meta: {
    family: string;
    image: string;
    width: number;
    height: number;
    durationInFrames: number;
    drawStart: number;
    drawEnd: number;
    brushInvisibleAfter: number;
    strokeCount: number;
  };
  strokes: SemanticStroke[];
};

const useSemanticRoutes = (src: string) => {
  const [data, setData] = useState<SemanticRoutes | null>(null);
  const [handle] = useState(() => delayRender(`semantic-painter:${src}`));
  useEffect(() => {
    let alive = true;
    fetch(staticFile(src))
      .then((res) => res.json())
      .then(async (raw: SemanticRoutes) => {
        if (!raw?.meta || !Array.isArray(raw.strokes) || raw.strokes.length < 1) {
          throw new Error("invalid semantic painter routes");
        }
        const decoded = new Image();
        decoded.decoding = "sync";
        decoded.src = staticFile(raw.meta.image);
        await decoded.decode();
        if (alive) setData(raw);
        continueRender(handle);
      })
      .catch((error) => {
        continueRender(handle);
        throw error;
      });
    return () => { alive = false; };
  }, [handle, src]);
  return data;
};

const pointAngle = (points: SemanticPoint[], index: number) => {
  const a = points[Math.max(0, index - 1)];
  const b = points[Math.min(points.length - 1, index + 1)];
  return Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI;
};

const PainterlyReveal: React.FC<{data: SemanticRoutes}> = ({data}) => {
  const frame = useCurrentFrame();
  const uid = useId().replace(/[:]/g, "");
  const maskId = `semantic-mask-${uid}`;

  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 10, pointerEvents: "none"}}>
    <defs>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
        <rect width={W} height={H} fill="#000" />
        <g>
          {data.strokes.flatMap((stroke, si) => stroke.points.slice(1).flatMap((point, index) => {
            const pi = index + 1;
            const pointFrame = stroke.start + (stroke.end - stroke.start) * (pi / Math.max(1, stroke.points.length - 1));
            if (frame < pointFrame) return [];
            const [x0, y0, pressure0] = stroke.points[pi - 1];
            const [x1, y1, pressure1] = point;
            const pressure = (pressure0 + pressure1) * 0.5;
            const width = stroke.width * pressure;
            const angle = Math.atan2(y1 - y0, x1 - x0);
            const jitter = Math.sin((si + 1) * 12.91 + pi * 3.17);
            const nx = -Math.sin(angle);
            const ny = Math.cos(angle);
            const offset = width * stroke.bristle * jitter;
            return <g key={`${stroke.id}-${pi}`} opacity={stroke.opacity}>
              <line x1={x0} y1={y0} x2={x1} y2={y1} stroke="#fff" strokeWidth={width}
                strokeLinecap="round" opacity="0.82" />
              <line x1={x0 + nx * offset} y1={y0 + ny * offset} x2={x1 + nx * offset} y2={y1 + ny * offset}
                stroke="#fff" strokeWidth={width * 0.28} strokeLinecap="round" opacity="0.46" />
              <line x1={x0 - nx * width * 0.22} y1={y0 - ny * width * 0.22}
                x2={x1 - nx * width * 0.22} y2={y1 - ny * width * 0.22}
                stroke="#fff" strokeWidth={width * 0.13} strokeLinecap="round" opacity="0.28" />
            </g>;
          }))}
        </g>
      </mask>
    </defs>
    <g transform="translate(0 0)" mask={`url(#${maskId})`}>
      <image href={staticFile(data.meta.image)} x={0} y={0} width={W} height={H} preserveAspectRatio="none" />
    </g>
  </svg>;
};

const activePose = (frame: number, strokes: SemanticStroke[]) => {
  const stroke = strokes.find((s) => frame >= s.start && frame <= s.end);
  if (!stroke) return null;
  const raw = Math.max(0, Math.min(1, (frame - stroke.start) / Math.max(0.001, stroke.end - stroke.start)));
  const index = Math.min(stroke.points.length - 1, Math.floor(raw * (stroke.points.length - 1)));
  const [x, y, pressure] = stroke.points[index];
  return {x, y, pressure, angle: pointAngle(stroke.points, index), group: stroke.group};
};

const PainterBrush: React.FC<{strokes: SemanticStroke[]}> = ({strokes}) => {
  const frame = useCurrentFrame();
  const pose = activePose(frame, strokes);
  if (!pose) return null;
  const glow = pose.group === "sunrise-rays" ? "#ffd47a" : "#7eeaff";
  const bristleScale = 0.78 + pose.pressure * 0.3;
  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 32, pointerEvents: "none"}}>
    <defs>
      <filter id="semantic-tip-glow" x="-400%" y="-400%" width="800%" height="800%">
        <feGaussianBlur stdDeviation="13" />
      </filter>
      <linearGradient id="semantic-handle" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#bcefff" />
        <stop offset="0.34" stopColor="#528db6" />
        <stop offset="1" stopColor="#15213b" />
      </linearGradient>
      <linearGradient id="semantic-bristles" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stopColor="#f9f0d4" />
        <stop offset="0.58" stopColor="#baa272" />
        <stop offset="1" stopColor="#342a24" />
      </linearGradient>
    </defs>
    <g transform={`translate(${pose.x} ${pose.y}) rotate(${pose.angle}) scale(${bristleScale})`}>
      <circle cx="0" cy="0" r="31" fill={glow} opacity="0.42" filter="url(#semantic-tip-glow)" />
      <path d="M-23 0 C-39 -15 -55 -17 -72 -10 L-76 10 C-54 18 -38 15 -23 0Z" fill="url(#semantic-bristles)" />
      <path d="M-78 -12 L-99 -15 L-99 15 L-78 12Z" fill="#7f95a8" />
      <path d="M-98 0 L-245 -50" stroke="rgba(0,0,0,0.35)" strokeWidth="25" strokeLinecap="round" transform="translate(7 8)" />
      <path d="M-98 0 L-245 -50" stroke="url(#semantic-handle)" strokeWidth="20" strokeLinecap="round" />
      <path d="M-16 -5 C-8 -3 -4 -2 0 0 C-5 3 -10 5 -18 6Z" fill={glow} opacity="0.9" />
    </g>
  </svg>;
};

const Stars: React.FC = () => {
  const frame = useCurrentFrame();
  return <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}
    style={{position: "absolute", inset: 0, zIndex: 3, pointerEvents: "none"}}>
    {Array.from({length: 30}, (_, i) => {
      const x = 42 + ((i * 263 + 71) % 1130);
      const y = 34 + ((i * 149 + 37) % 680);
      const r = 0.7 + (i % 5) * 0.3;
      const pulse = 0.24 + 0.56 * (0.5 + 0.5 * Math.sin(frame * (0.032 + (i % 3) * 0.009) + i * 1.5));
      return <circle key={i} cx={x} cy={y} r={r} fill={i % 7 === 0 ? "#ffd58a" : "#ddf8ff"} opacity={pulse} />;
    })}
  </svg>;
};

const Copy: React.FC = () => {
  const frame = useCurrentFrame();
  const titleOpacity = interpolate(frame, [8, 24], [0, 1], clamp);
  const cueOpacity = Math.min(interpolate(frame, [220, 236], [0, 1], clamp), interpolate(frame, [260, 274], [1, 0], clamp));
  return <AbsoluteFill style={{zIndex: 70, pointerEvents: "none", transform: "translateZ(0)"}}>
    <div style={{position: "absolute", left: 72, top: 58, opacity: titleOpacity,
      fontFamily: '-apple-system, "Apple SD Gothic Neo", sans-serif', color: "#f1f8ff"}}>
      <div style={{fontSize: 15, fontWeight: 850, letterSpacing: 4.4, color: "#8fe9ff"}}>SEMANTIC PAINTER FLOW</div>
      <div style={{fontSize: 38, fontWeight: 780, marginTop: 8, letterSpacing: -1.1,
        textShadow: "0 0 22px rgba(85,205,255,0.3)"}}>03. 궤도 위 일출</div>
    </div>
    <div style={{position: "absolute", left: 0, right: 0, bottom: 54, opacity: cueOpacity,
      display: "flex", justifyContent: "center", fontFamily: '-apple-system, "Apple SD Gothic Neo", sans-serif'}}>
      <div style={{padding: "13px 30px 14px", borderRadius: 999, color: "#eef8ff", fontSize: 31,
        fontWeight: 700, background: "rgba(2,7,20,0.58)", border: "1px solid rgba(143,233,255,0.2)",
        backdropFilter: "blur(5px)", boxShadow: "0 10px 34px rgba(0,0,0,0.28)"}}>
        빛이 지구의 곡선을 천천히 깨운다
      </div>
    </div>
  </AbsoluteFill>;
};

export const CosmicSemanticPainterDemo: React.FC = () => {
  const frame = useCurrentFrame();
  const data = useSemanticRoutes("cosmic-dark-pilot/semantic-painter-routes.json");
  const settle = interpolate(frame, [220, 250], [0, 1], clamp);
  const outro = interpolate(frame, [272, 299], [0, 1], clamp);
  return <AbsoluteFill style={{backgroundColor: CANVAS, overflow: "hidden"}}>
    <AbsoluteFill style={{backgroundImage:
      "radial-gradient(circle at 77% 55%, rgba(39,91,176,0.14), transparent 43%), radial-gradient(circle at 18% 14%, rgba(86,61,147,0.08), transparent 38%)"}} />
    <Stars />
    {data && <PainterlyReveal data={data} />}
    {data && <PainterBrush strokes={data.strokes} />}
    <AbsoluteFill style={{zIndex: 21, pointerEvents: "none", opacity: settle * 0.055,
      background: "radial-gradient(circle at 84% 42%, rgba(255,193,87,0.52), transparent 22%)"}} />
    <Copy />
    <AbsoluteFill style={{zIndex: 90, pointerEvents: "none", backgroundColor: CANVAS, opacity: outro}} />
  </AbsoluteFill>;
};

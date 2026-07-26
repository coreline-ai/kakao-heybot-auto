// Zod 스키마 v1 — 이 파일이 render-props의 유일한 진실이다.
// 기본값(faint 0.6 등)도 여기에서만 정의한다. 컴포넌트는 parse된 값을 그대로 소비한다.
// JSON Schema(schema/render-props.schema.json)는 scripts/export-schema.ts로 여기서 내보내며,
// 파이썬 파이프라인은 그 산출물을 소비만 한다 (이중 정의 금지).
import { z } from "zod";

export const SCHEMA_VERSION = 1;
export const FPS = 30;

const frame = z.number().int().min(0);
// routes의 스트로크 타이밍은 소수 프레임(예: 8.07)이다 — 실측(winter-snow-pine-demo) 기준. int 금지.
const fractionalFrame = z.number().min(0);

// ---------- routes JSON (참조 시스템과 포맷 호환 — 골든 샘플 재사용을 위해) ----------

export const StrokeSchema = z.object({
  id: z.string(),
  kind: z.string(), // "contour" | "seal" 등 — 렌더는 구분하지 않으므로 열어둔다
  width: z.number().positive(),
  start: fractionalFrame,
  end: fractionalFrame,
  points: z.array(z.tuple([z.number(), z.number()])).min(1),
});

export const RoutesMetaSchema = z
  .object({
    image: z.string(), // public/ 기준 상대경로 (staticFile 규약)
    width: z.number().positive(),
    height: z.number().positive(),
    fps: z.number().positive(),
    durationInFrames: fractionalFrame,
    drawStart: fractionalFrame,
    drawEnd: fractionalFrame,
    penInvisibleAfter: fractionalFrame,
    routeCount: z.number().int().min(0),
  })
  .passthrough(); // coverage 등 부가 메타는 통과

export const RoutesDataSchema = z.object({
  meta: RoutesMetaSchema,
  strokes: z.array(StrokeSchema),
});

// ---------- cosmic-random-brush routes JSON ----------
// 기존 2D point routes 계약을 느슨하게 바꾸지 않고 별도 schema로 격리한다.

export const RandomTouchPointSchema = z.tuple([
  z.number(), z.number(), z.number().min(0).max(1),
]);

export const RandomTouchStrokeSchema = z.object({
  id: z.string(),
  kind: z.literal("random-touch"),
  width: z.number().positive(),
  start: fractionalFrame,
  end: fractionalFrame,
  opacity: z.number().min(0).max(1),
  dryness: z.number().min(0).max(1),
  points: z.array(RandomTouchPointSchema).min(2),
});

export const RandomTouchRoutesMetaSchema = z.object({
  family: z.literal("free-random-touch"),
  image: z.string(),
  width: z.number().positive(),
  height: z.number().positive(),
  fps: z.literal(30),
  durationInFrames: z.literal(300),
  drawStart: fractionalFrame,
  drawEnd: fractionalFrame,
  settleStart: fractionalFrame,
  settleEnd: fractionalFrame,
  brushInvisibleAfter: fractionalFrame,
  strokeCount: z.number().int().min(37).max(56),
  baseStrokeCount: z.literal(36),
  coverageStrokeCount: z.number().int().min(1).max(20),
  targetMaskCoverage: z.number().min(0.991).max(1),
  maskCoverage: z.number().min(0.991).max(1),
  contentAnalysisVersion: z.literal("luma-chroma-v1").optional(),
  visibleContentFraction: z.number().min(0.001).max(1).optional(),
  visibleContentCoverage: z.number().min(0.985).max(1).optional(),
  brushWidthRange: z.tuple([z.number().positive(), z.number().positive()]),
  meanCenterJump: z.number().min(650),
  maxCenterJump: z.number().min(1200),
  seed: z.number().int(),
  deterministic: z.literal(true),
}).superRefine((meta, ctx) => {
  if (meta.strokeCount !== meta.baseStrokeCount + meta.coverageStrokeCount) {
    ctx.addIssue({code: z.ZodIssueCode.custom, path: ["strokeCount"],
      message: "strokeCount는 baseStrokeCount + coverageStrokeCount여야 함"});
  }
  if (!(meta.drawStart < meta.drawEnd && meta.drawEnd <= meta.brushInvisibleAfter
        && meta.brushInvisibleAfter < meta.settleStart && meta.settleStart < meta.settleEnd
        && meta.settleEnd < meta.durationInFrames)) {
    ctx.addIssue({code: z.ZodIssueCode.custom,
      message: "random touch timing 순서가 올바르지 않음"});
  }
});

export const RandomTouchRoutesDataSchema = z.object({
  meta: RandomTouchRoutesMetaSchema,
  strokes: z.array(RandomTouchStrokeSchema),
}).superRefine((data, ctx) => {
  if (data.strokes.length !== data.meta.strokeCount) {
    ctx.addIssue({code: z.ZodIssueCode.custom, path: ["strokes"],
      message: "strokes 길이는 meta.strokeCount와 같아야 함"});
  }
  const coordinateScale = data.meta.width / 1920;
  const minBrush = 230 * coordinateScale;
  const maxBrush = 365 * coordinateScale;
  data.strokes.forEach((stroke, index) => {
    if (stroke.width < minBrush || stroke.width > maxBrush) {
      ctx.addIssue({code: z.ZodIssueCode.custom, path: ["strokes", index, "width"],
        message: `width는 현재 캔버스 비율 범위 ${minBrush}~${maxBrush}여야 합니다`});
    }
  });
});

// ---------- 씬 구성 요소 ----------

export const CueSchema = z.object({
  text: z.string().min(1),
  from: frame,
  to: frame,
});

export const BrushSchema = z
  .object({
    kind: z.enum(["image", "pen"]).default("image"), // pen = 내장 벡터 펜 스프라이트 (src 불필요)
    src: z.string().optional(), // 커서 이미지 (public/ 기준 상대경로) — kind: image일 때 필수
    w: z.number().positive().optional(), // pen은 기본 140
    h: z.number().positive().optional(),
    tipx: z.number().optional(), // 붓끝 픽셀 좌표 (디스플레이 px)
    tipy: z.number().optional(),
    visible: z.boolean().default(true),
    opacity: z.number().min(0).max(1).default(1),
  })
  .refine((b) => b.kind === "pen" || (b.src != null && b.w != null && b.h != null && b.tipx != null && b.tipy != null), {
    message: 'kind "image" 커서는 src/w/h/tipx/tipy가 필수다',
  });

export const BrushDynamicsSchema = z.object({
  drawSpeedScale: z.number().positive().default(1), // >1이면 더 천천히 그림 (1.12~1.18 권장)
  touchScale: z.number().positive().default(1), // stroke width 배율
  touchJitter: z.number().min(0).default(0), // width 랜덤 편차 (0.18 = ±18%)
  pathJitter: z.number().min(0).default(0), // path 흔들림(px)
  randomizeOrder: z.boolean().default(false),
  randomReverse: z.boolean().default(false),
  seed: z.number().int().default(1),
});

export const TopTitleSchema = z.object({
  kicker: z.string().optional(),
  lines: z.array(z.string().min(1)).min(1),
  x: z.number().optional(),
  y: z.number().optional(),
  width: z.number().optional(),
  align: z.enum(["left", "center", "right"]).default("left"),
  enterAt: frame.default(0),
  accent: z.string().optional(),
  firstWordColor: z.string().optional(), // 배경에서 추출한 인상색
  color: z.string().optional(), // 다크 프로파일용 제목 본문색
  fontSize: z.number().optional(),
  kickerFontSize: z.number().optional(),
  wash: z.boolean().default(false),
});

export const SubtitleStyleSchema = z.object({
  bottom: z.number().optional(),
  fontSize: z.number().optional(),
  maxWidth: z.number().optional(),
  paddingX: z.number().optional(),
  paddingY: z.number().optional(),
  color: z.string().optional(),
  highlightColor: z.string().optional(),
  background: z.string().optional(),
  border: z.string().optional(),
});

export const NaturalEffectsSchema = z.object({
  kind: z.enum(["mist", "forestDust", "streamSparkle", "meadowWind", "sunsetGlow", "starTwinkle"]),
  opacity: z.number().min(0).max(1).default(0.05),
  parallaxScale: z.number().default(1),
  seed: z.number().int().default(1),
  endFadeOpacity: z.number().min(0).max(1).optional(),
});

export const DrawingPhaseSchema = z
  .object({
    kind: z.enum(["outline", "paint"]),
    routes: z.string().min(1),
    cursor: BrushSchema.optional(),
    zIndex: z.number().int().default(10),
    edgeFeather: z.number().min(0).default(0),
    fadeOutFrom: frame.optional(),
    fadeOutTo: frame.optional(),
  })
  .superRefine((phase, ctx) => {
    if ((phase.fadeOutFrom == null) !== (phase.fadeOutTo == null)) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "fadeOutFrom/fadeOutTo는 함께 지정해야 함" });
    }
    if (phase.fadeOutFrom != null && phase.fadeOutTo != null && phase.fadeOutTo <= phase.fadeOutFrom) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: "fadeOutTo는 fadeOutFrom보다 커야 함" });
    }
  });

// ---------- 위젯 (단일 registry 핵심 15종 — strict union: 타입별 필드 혼입은 parse 거부) ----------

const widgetBase = {
  x: z.number(),
  y: z.number(),
  w: z.number().positive(),
  h: z.number().positive(),
  enterAt: frame.default(0),
  title: z.string().min(1),
  kicker: z.string().optional(),
  caption: z.string().optional(),
  accent: z.string().optional(),
};

export const WidgetItemSchema = z.object({
  label: z.string(),
  detail: z.string().optional(),
  value: z.union([z.string(), z.number()]).optional(),
  tone: z.enum(["ok", "warn", "danger"]).optional(),
});
const items = z.array(WidgetItemSchema).default([]);

// 카드형 공통 스펙을 쓰는 카탈로그 타입 (실사용 빈도 상위 11종)
export const CARD_WIDGET_TYPES = [
  "FlowDiagram", "TimelineStepper", "DataTable", "ProcessStepCard", "WarningCard",
  "PersonAvatar", "ChatBubble", "CompareBars", "BulletList", "QuoteText", "Headline",
] as const;

export const WidgetSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("stat"), ...widgetBase, value: z.union([z.string(), z.number()]), sub: z.string().optional() }).strict(),
  z.object({ type: z.literal("text"), ...widgetBase, lines: z.array(z.string().min(1)).min(1) }).strict(),
  z.object({ type: z.literal("donut"), ...widgetBase, pct: z.number().min(0).max(100) }).strict(),
  z.object({ type: z.literal("bars"), ...widgetBase, values: z.array(z.number().min(0)).min(1) }).strict(),
  ...CARD_WIDGET_TYPES.map((t) => z.object({ type: z.literal(t), ...widgetBase, items }).strict()),
]);

// ---------- 씬 ----------

// 원본 프레임의 시간적 진행 또는 풀블리드 스토리 장면은 route 재생성 없이
// 원본 bitmap을 그대로 보존한다. 기존 brush/pen route 계약과는 분리한다.
export const FullBleedPresentationSchema = z.enum([
  "progressive-frame-sequence",
  "storybook-full-bleed",
]);

export const SceneSchema = z.object({
  id: z.string().min(1),
  routes: z.string().optional(), // routes JSON 경로 (public/ 기준). 없으면 렌더가 명시적 에러 표시
  drawingPhases: z.array(DrawingPhaseSchema).length(2).optional(),
  // pen-brush의 outline·paint·cursor를 같은 비율로 확대해 외곽 종이 여백을 크롭한다.
  viewportScale: z.number().min(1).max(1.35).default(1),
  presentation: FullBleedPresentationSchema.optional(),
  image: z.string().min(1).optional(), // presentation 전용 full-bleed bitmap
  previousImage: z.string().min(1).optional(), // scene 시작 cross-dissolve의 이전 bitmap
  durationInFrames: z.number().int().positive(),

  // 리빌 튜닝 — 기본값은 참조 시스템의 튜닝 결과를 채택
  faint: z.number().min(0).max(1).default(0.6), // 그리는 중 스트로크 레이어 불투명도
  edgeFeather: z.number().min(0).default(0), // 리빌 가장자리 blur(px). 0=하드
  linearDraw: z.boolean().default(false), // true면 이징 없이 등속 드로잉
  developFrames: frame.optional(), // 미지정 시 penInvisibleAfter→duration 구간 사용
  completionMode: z.enum(["develop", "masked-hold", "integrated-develop"]).default("develop"),
  // 전체 이미지가 채워진 뒤 채도·대비만 천천히 정착시키는 시간.
  // 누락 영역 채움과 색 보정을 분리해 완성 순간의 밝기 펄스를 방지한다.
  colorSettleFrames: frame.default(36),

  // 드로잉 아래에 유지되는 희미한 원본 가이드. 첫 정지 프레임이 빈 종이만 보이지 않게 한다.
  previewOpacity: z.number().min(0).max(1).default(0),

  // prewash (씬 시작 시 흐린 원본 예고)
  // routes 이미지와 다른 정규화 원본을 카드용 첫 화면에 쓸 때만 지정한다.
  prewashImage: z.string().min(1).optional(),
  prewashOpacity: z.number().min(0).max(1).default(0),
  prewashFrames: frame.default(0),
  prewashHoldFrames: frame.default(6),
  // 0이면 기존 prewashFrames 전체를 hold+fade에 사용한다. 양수이면
  // prewashFrames는 드로잉 지연, 이 값은 재생 시작 직후 fade-out 길이다.
  prewashFadeOutFrames: frame.default(0),
  prewashBlur: z.number().min(0).default(12),

  // outro (씬 끝 종이 dissolve)
  outroFadeFrames: frame.default(0), // 0이면 비활성
  outroWashOpacity: z.number().min(0).max(1).default(0.88),
  outroBlur: z.number().min(0).default(0),

  brushDynamics: BrushDynamicsSchema.optional(),
  cues: z.array(CueSchema).default([]),
  captionsVisible: z.boolean().default(true),
  topTitle: TopTitleSchema.optional(),
  subtitleStyle: SubtitleStyleSchema.optional(),
  naturalEffects: NaturalEffectsSchema.optional(),
  widgets: z.array(WidgetSchema).default([]),
}).superRefine((scene, ctx) => {
  if (scene.drawingPhases) {
    if (scene.drawingPhases[0]?.kind !== "outline" || scene.drawingPhases[1]?.kind !== "paint") {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["drawingPhases"],
        message: "drawingPhases는 outline → paint 정확히 2단계여야 함" });
    }
  } else if (!scene.routes) {
    if (scene.presentation && !scene.image) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, path: ["image"],
        message: "full-bleed presentation에는 image가 필요합니다" });
    }
    // 기존 렌더의 화면 내 오류 표시는 유지하되, props 계약에서는 누락을 허용한다.
  }
});

// ---------- 프로젝트 전체 (render-props) ----------

export const RenderPropsSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  projectId: z.string().min(1),
  title: z.string().optional(),
  format: z.enum(["youtube", "shorts"]).default("youtube"),
  audio: z.string().nullable().default(null), // null = 영상만 렌더 (이후 mux)
  paper: z.string().default("#fbfaf6"),
  brush: BrushSchema.optional(),
  scenes: z.array(SceneSchema).min(1),
});

// ---------- Full Color Motion ----------
// 붓/펜 리빌과 달리 원본 색상을 보존한 정지 이미지를 2D Ken-Burns 이동과
// 효과로 연출하는 별도 계약이다. RenderPropsSchema에 routes/faint 같은 붓 전용
// 필드를 섞지 않아 두 제품 라인의 기본값 드리프트를 막는다.

export const MotionMovementSchema = z.enum([
  "push-in",
  "push-out",
  "pan-left",
  "pan-right",
  "rise",
  "fall",
  "arc-left",
  "arc-right",
]);

export const MotionEffectSchema = z.enum([
  "none",
  "rays",
  "mist",
  "birds",
  "stars",
  "storm",
  "sparkles",
  "lanterns",
  "fireflies",
  "ripples",
  "aurora",
  "trail",
]);

export const MotionRevealSchema = z.object({
  mode: z.enum(["none", "brush"]).default("none"),
  // brush 모드일 때 pipeline routes stage가 생성하는 public-relative JSON 경로.
  routes: z.string().min(1).optional(),
  frames: z.number().int().min(30).max(180).default(96),
  cursor: BrushSchema.optional(),
}).superRefine((reveal, ctx) => {
  if (reveal.mode === "brush" && !reveal.routes) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["routes"],
      message: "motion reveal.mode=brush에는 routes가 필요합니다",
    });
  }
});

export const FullColorMotionSceneSchema = z.object({
  id: z.string().min(1),
  image: z.string().min(1),
  durationInFrames: z.number().int().positive(),
  movement: MotionMovementSchema.default("push-in"),
  effect: MotionEffectSchema.default("none"),
  intensity: z.number().min(0.25).max(2).default(1),
  reveal: MotionRevealSchema.default({ mode: "none", frames: 96 }),
  cues: z.array(CueSchema).default([]),
  captionsVisible: z.boolean().default(true),
  topTitle: TopTitleSchema.optional(),
  subtitleStyle: SubtitleStyleSchema.optional(),
});

export const FullColorMotionPropsSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  projectId: z.string().min(1),
  title: z.string().optional(),
  format: z.enum(["youtube", "shorts"]).default("youtube"),
  // 영상은 audio:null로 렌더한 뒤 기존 mix/mux stage가 AAC를 결합한다.
  audio: z.string().nullable().default(null),
  brush: BrushSchema.optional(),
  scenes: z.array(FullColorMotionSceneSchema).min(1),
});

export type Stroke = z.infer<typeof StrokeSchema>;
export type RoutesData = z.infer<typeof RoutesDataSchema>;
export type RandomTouchPoint = z.infer<typeof RandomTouchPointSchema>;
export type RandomTouchStroke = z.infer<typeof RandomTouchStrokeSchema>;
export type RandomTouchRoutesData = z.infer<typeof RandomTouchRoutesDataSchema>;
export type Cue = z.infer<typeof CueSchema>;
export type Brush = z.infer<typeof BrushSchema>;
export type BrushDynamics = z.infer<typeof BrushDynamicsSchema>;
export type TopTitle = z.infer<typeof TopTitleSchema>;
export type SubtitleStyle = z.infer<typeof SubtitleStyleSchema>;
export type NaturalEffects = z.infer<typeof NaturalEffectsSchema>;
export type DrawingPhase = z.infer<typeof DrawingPhaseSchema>;
export type FullBleedPresentation = z.infer<typeof FullBleedPresentationSchema>;
export type Scene = z.infer<typeof SceneSchema>;
export type RenderProps = z.infer<typeof RenderPropsSchema>;
export type Widget = z.infer<typeof WidgetSchema>;
export type WidgetItem = z.infer<typeof WidgetItemSchema>;
export type CardWidget = Extract<Widget, { items: WidgetItem[] }>;
export type MotionMovement = z.infer<typeof MotionMovementSchema>;
export type MotionEffect = z.infer<typeof MotionEffectSchema>;
export type MotionReveal = z.infer<typeof MotionRevealSchema>;
export type FullColorMotionScene = z.infer<typeof FullColorMotionSceneSchema>;
export type FullColorMotionProps = z.infer<typeof FullColorMotionPropsSchema>;

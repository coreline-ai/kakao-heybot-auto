// 다크 프로모 위젯 스키마 — KIMI-K3 분석 W1–W36 census의 재현 계약 (W30 게임 HUD·W35 3D 구체 제외).
// 기존 WidgetSchema(화이트 카드 15종)와 분리된 discriminated union이다. 패턴은 동형(strict + 기본값).
import { z } from "zod";

const frame = z.number().int().min(0);
const rel = z.number().min(0).max(1); // 위젯 내부 상대 좌표

// 공통 배치 — 절대 px 좌표(컴포지션 기준). enterAt 프레임부터 등장 모션 시작.
// entrance (P6-B): rise=기본 정착 / brushIn=붓질 마스크 리빌 (이 레포 수묵 문법의 위젯 이식)
const promoBase = {
  x: z.number(),
  y: z.number(),
  w: z.number().positive(),
  h: z.number().positive(),
  enterAt: frame.default(0),
  entrance: z.enum(["rise", "brushIn"]).default("rise"),
  label: z.string().optional(), // 위젯 상단 소형 라벨 (예: "OFFICIAL WEIGH-IN")
};

// ---------- 패밀리 1·2·3·4 코어 ----------

// W1a 니들 / W1b 필-아크 게이지
const GaugeSchema = z
  .object({
    type: z.literal("gauge"),
    ...promoBase,
    kind: z.enum(["needle", "fill-arc"]).default("needle"),
    value: z.number(),
    min: z.number().default(0),
    max: z.number(),
    unit: z.string().optional(), // 값 병기 단위 (예: "T", "%")
    ticks: z.number().int().min(0).max(60).default(24), // 아크 눈금/스포크 개수
    goldTail: z.boolean().default(false), // fill-arc 끝단 골드 테일 (원본 캐시율 게이지)
    caption: z.string().optional(), // 값 하단 캡션 pill (예: "CODING")
    sweepFrames: frame.default(36),
  })
  .strict();

// W2 진행 바 / W3 VS 대결 바 (mode)
const StatBarSchema = z
  .object({
    type: z.literal("statBar"),
    ...promoBase,
    mode: z.enum(["progress", "vs"]).default("progress"),
    value: z.number(), // progress: 현재값 / vs: 주체 점수
    max: z.number(),
    name: z.string().optional(), // vs: 주체 이름 (예: "KIMI K3")
    ticks: z.array(z.object({ at: z.number(), label: z.string() }).strict()).default([]), // 축 눈금
    endLabel: z.string().optional(), // 우단 골드 라벨 (예: "1M TOKENS")
    rivals: z.array(z.object({ name: z.string(), value: z.number() }).strict()).default([]), // vs 전용
    fillFrames: frame.default(30),
  })
  .strict();

// W5 리더보드 — 최종 순위 순 rows. highlight 행은 풀폭 블루 필 + (reorder 시) 하단에서 상승.
const LeaderboardSchema = z
  .object({
    type: z.literal("leaderboard"),
    ...promoBase,
    header: z.string().optional(), // pill 헤더 (예: "PROGRAM BENCH")
    headerTag: z.string().optional(), // 헤더 우측 보조 (예: "ALL MODELS")
    rows: z
      .array(
        z.object({ name: z.string().min(1), score: z.number(), highlight: z.boolean().default(false) }).strict(),
      )
      .min(1),
    footer: z.string().optional(), // 하단 중앙 (예: "NEW LEADER")
    scoreDecimals: z.number().int().min(0).max(3).default(1),
    reorder: z.boolean().default(false), // highlight 행이 최하단 위치에서 자기 순위로 상승
    populateFrames: frame.default(8), // 행당 진입 stagger
  })
  .strict();

// W8 카운트업 / W9 배수(prefix "×") — 오도미터 히어로 숫자
const CountUpSchema = z
  .object({
    type: z.literal("countUp"),
    ...promoBase,
    from: z.number().default(0),
    to: z.number(),
    decimals: z.number().int().min(0).max(3).default(1),
    prefix: z.string().optional(), // 예: "×", "$", "+"
    suffix: z.string().optional(), // 예: "T", "%", " PTS"
    suffixAccent: z.boolean().default(true), // suffix를 데이터 블루로
    rule: z.boolean().default(false), // 하단 헤어라인 룰
    caption: z.string().optional(), // 예: "TRILLION PARAMETERS"
    countFrames: frame.default(24),
  })
  .strict();

// W6 수직 랭크 래더 — #15→#2 지그재그 + 등반 pill 토큰
const RankLadderSchema = z
  .object({
    type: z.literal("rankLadder"),
    ...promoBase,
    ranks: z.array(z.number().int()).min(2), // 아래→위 순 눈금 (예: [15,12,9,6,4,3])
    climbTo: z.number().int(), // 토큰 최종 순위 (예: 2)
    climbFrames: frame.default(40),
  })
  .strict();

// W7 수직선/ELO 플롯 — 축 + 점(tone) + CLOSING 점선 화살표
const NumberLinePlotSchema = z
  .object({
    type: z.literal("numberLinePlot"),
    ...promoBase,
    axisLabel: z.string().optional(), // 예: "GDPval ELO"
    min: z.number(),
    max: z.number(),
    points: z
      .array(
        z.object({ name: z.string(), value: z.number(), tone: z.enum(["data", "rival", "muted"]).default("muted") }).strict(),
      )
      .min(1),
    arrowFrom: z.string().optional(), // 점 name
    arrowTo: z.string().optional(),
    arrowLabel: z.string().optional(), // 예: "CLOSING"
    drawFrames: frame.default(30),
  })
  .strict();

// W10 가격표 — 행잉 태그 펜듈럼 + 고스트 잔상 드롭
const PriceTagSchema = z
  .object({
    type: z.literal("priceTag"),
    ...promoBase,
    value: z.string().min(1), // 예: "$3.00"
    sub: z.string().optional(), // 예: "PER 1M TOKENS"
    ghostValue: z.string().optional(), // 위 취소선 잔상 (예: "$3.00" → value "$0.30")
    swing: z.boolean().default(true),
  })
  .strict();

// ---------- 패밀리 5 데이터 패널 ----------

// W11 노드/네트워크 그래프 — 보더 박스 + 노드 + 커넥터 draw-on
const NodeGraphSchema = z
  .object({
    type: z.literal("nodeGraph"),
    ...promoBase,
    pill: z.string().optional(), // 좌상 pill (예: "/swarm")
    footer: z.string().optional(), // 중앙 푸터 (예: "PARALLEL AGENTS")
    nodes: z.array(z.object({ label: z.string(), x: rel, y: rel }).strict()).min(1),
    edges: z.array(z.tuple([z.number().int().min(0), z.number().int().min(0)])).default([]),
    drawFrames: frame.default(24),
  })
  .strict();

// W12 어텐션 곡선 플롯 — dashed 베이스라인 + 곡선 draw-on + × 마커
const CurvePlotSchema = z
  .object({
    type: z.literal("curvePlot"),
    ...promoBase,
    baselineLabel: z.string().optional(), // 예: "STANDARD ROUTE"
    points: z.array(z.object({ x: rel, y: rel }).strict()).min(2), // 좌→우, y=0 상단
    markers: z.array(z.number().int().min(0)).default([]), // × 마커 point 인덱스
    drawFrames: frame.default(36),
  })
  .strict();

// W13 히트맵 그리드 — 셀 순차 점등 + 우측 값
const HeatmapGridSchema = z
  .object({
    type: z.literal("heatmapGrid"),
    ...promoBase,
    cols: z.number().int().min(1).max(40),
    rows: z.number().int().min(1).max(40),
    lit: z.number().int().min(0), // 최종 점등 셀 수
    value: z.string().optional(), // 우측 히어로 값 (예: "89.3")
    litFrames: frame.default(36),
  })
  .strict();

// W14 전문가 파티클 필드 — 결정적 스캐터 + 활성 카운터
const ParticleFieldSchema = z
  .object({
    type: z.literal("particleField"),
    ...promoBase,
    count: z.number().int().min(1).max(1000).default(200),
    activeTarget: z.number().int().min(0), // 최종 활성 도트 수
    counterLabel: z.string().optional(), // 예: "ROSTER"
    activateFrames: frame.default(40),
  })
  .strict();

// W15 오실로스코프 — 메인 chirp 파형 스크롤 + 소 레퍼런스 패널
const OscilloscopeSchema = z
  .object({
    type: z.literal("oscilloscope"),
    ...promoBase,
    mainLabel: z.string().min(1), // 예: "REC · MON-A · GW STRAIN FEED"
    refLabel: z.string().optional(), // 예: "MON-B · TEMPLATE BANK"
    pill: z.string().optional(), // 하단 pill (예: "LAB ASSISTANT · GRAVITATIONAL-WAVE RUN")
  })
  .strict();

// W16 플로우 다이어그램 — 노드 순차 활성 (FETCH→COMPUTE→WRITE)
const FlowDiagramSchema = z
  .object({
    type: z.literal("flowDiagram"),
    ...promoBase,
    steps: z.array(z.string().min(1)).min(2),
    stepFrames: frame.default(14), // 스텝당 활성 지속
  })
  .strict();

// W17 지층/터널 다이어그램 — 해치 레이어 박스 + 하단 튜브 도트 주행
const StrataDiagramSchema = z
  .object({
    type: z.literal("strataDiagram"),
    ...promoBase,
    title: z.string().min(1), // 예: "TRANSFORMER ERA 2017-"
    dots: z.number().int().min(1).max(8).default(3),
    travelFrames: frame.default(60),
  })
  .strict();

// ---------- 패밀리 6 UI 크롬 ----------

// W18 로그/기록 카드 — 헤더 양끝(제목/● REC) + 타자기 엔트리 + 고스트 슬롯
const LogCardSchema = z
  .object({
    type: z.literal("logCard"),
    ...promoBase,
    title: z.string().min(1), // 예: "SEASON 2026 · MATCH RECORD"
    rec: z.boolean().default(true), // 우측 ● REC 표시
    activeEntry: z.string().min(1), // 타자기로 입력되는 첫 엔트리
    ghostCount: z.number().int().min(0).max(8).default(3), // ENTRY 00N 고스트 슬롯 수
    footer: z.string().optional(), // 예: "OBSERVATION LOG // NOW RECORDING"
    typeFrames: frame.default(30),
  })
  .strict();

// W19 터미널 — 부트 라인 + 프롬프트 타이핑 + 커서 블링크 + 상태 라인
const TerminalSchema = z
  .object({
    type: z.literal("terminal"),
    ...promoBase,
    header: z.string().optional(), // 예: "KIMI CODE — BOOT SEQUENCE"
    bootLines: z.array(z.string()).default([]), // 예: ["K3 CORE LINKED", "AGENT RUNTIME OK"]
    prompt: z.string().default("$"),
    command: z.string().min(1), // 예: "kimi"
    status: z.string().optional(), // 타이핑 완료 후 표시 (예: "K3 READY")
    typeFrames: frame.default(24),
  })
  .strict();

// W20 체크리스트/상태 패널 — 행별 populate + status 플래그
const ChecklistPanelSchema = z
  .object({
    type: z.literal("checklistPanel"),
    ...promoBase,
    items: z.array(z.object({ name: z.string().min(1), status: z.string().default("READY") }).strict()).min(1),
    footer: z.string().optional(), // 예: "SCORING SYSTEM ONLINE"
    populateFrames: frame.default(8),
  })
  .strict();

// W21 번호 콜아웃 패널 — 01/02/03 리스트 + 리더 라인 draw-on + 타깃 레티클
const CalloutPanelSchema = z
  .object({
    type: z.literal("calloutPanel"),
    ...promoBase,
    header: z.string().min(1), // 예: "OBSERVER · INSPECT"
    items: z.array(z.string().min(1)).min(1),
    reticle: z.object({ x: rel, y: rel }).strict().optional(), // 위젯 내 타깃 지점
    drawFrames: frame.default(24),
  })
  .strict();

// W22 플랫폼 셀렉터 — 대형 선택 카드 + 옵션 스택 + 커넥터
const PlatformSelectorSchema = z
  .object({
    type: z.literal("platformSelector"),
    ...promoBase,
    primary: z.string().min(1), // 예: "WEB"
    options: z.array(z.string().min(1)).min(1), // 예: ["APP", "API"]
    footer: z.string().optional(), // 예: "AVAILABLE NOW"
    staggerFrames: frame.default(10),
  })
  .strict();

// ---------- 패밀리 7 라벨/배지/모션 크롬 ----------

// W23 키커 오버라인 — 좌상 대문자 모노 + 틱 + pill
const KickerSchema = z
  .object({
    type: z.literal("kicker"),
    ...promoBase,
    text: z.string().min(1), // 예: "CHALLENGER // APPROACHING"
    tick: z.boolean().default(true), // 앞 블루 틱
    pill: z.string().optional(), // 뒤 pill 배지
  })
  .strict();

// W24 pill/태그 배지
const PillBadgeSchema = z
  .object({
    type: z.literal("pillBadge"),
    ...promoBase,
    text: z.string().min(1),
    tone: z.enum(["neutral", "data", "cta"]).default("neutral"),
    glyph: z.string().optional(), // 앞 글리프 (예: "◈")
  })
  .strict();

// W25 스포츠 콜아웃 — WINNER 스탬프 / ×2 콤보 / 골드 배너 / UPGRADED 스탬프
const SportsCalloutSchema = z
  .object({
    type: z.literal("sportsCallout"),
    ...promoBase,
    variant: z.enum(["winner", "multiKill", "banner", "stamp"]),
    text: z.string().min(1), // 예: "WINNER", "×2", "BONUS ROUND", "UPGRADED"
    sub: z.string().optional(), // multiKill 상단 소형 (예: "COMBO")
  })
  .strict();

// W26 마퀴 티커 — 하단 풀폭 반복 스크롤
const MarqueeSchema = z
  .object({
    type: z.literal("marquee"),
    ...promoBase,
    text: z.string().min(1), // 예: "MATCH HIGHLIGHTS"
    separator: z.string().default(" ▸ "),
    speed: z.number().positive().default(3), // px/frame
  })
  .strict();

// W27 스플릿플랩 보드 — 글자 타일 플립
const SplitFlapSchema = z
  .object({
    type: z.literal("splitFlap"),
    ...promoBase,
    text: z.string().min(1), // 예: "PRICING"
    staggerFrames: frame.default(4), // 글자당 시작 지연
    flipFrames: frame.default(16), // 글자당 플립 지속
  })
  .strict();

// W28 타임라인 스크러버 — 기간 압축 (2 WEEKS → 2 HOURS)
const TimelineScrubberSchema = z
  .object({
    type: z.literal("timelineScrubber"),
    ...promoBase,
    fromLabel: z.string().min(1),
    toLabel: z.string().min(1),
    scrubFrames: frame.default(40),
  })
  .strict();

// W29 코너 프레이밍 브래킷 — 4코너 + 상단 라벨 + 중앙 라벨
const FrameBracketsSchema = z
  .object({
    type: z.literal("frameBrackets"),
    ...promoBase,
    topLeft: z.string().optional(), // 예: "NOW SHOWING · WUXIA RPG"
    topRight: z.string().optional(), // 예: "TITLE SCREEN INCLUDED"
    centerLabel: z.string().optional(), // 예: "PRESS START"
  })
  .strict();

// W31 자막 밴드 — 중앙 하단 고정 + 어둠 그라디언트
const SubtitleSchema = z
  .object({
    type: z.literal("subtitle"),
    ...promoBase,
    text: z.string().min(1),
  })
  .strict();

// W32 셰브론/트로피 배지 — 페넌트 3분할 (CODING/AGENTS/VISUAL SOTA)
const ChevronBadgesSchema = z
  .object({
    type: z.literal("chevronBadges"),
    ...promoBase,
    items: z
      .array(z.object({ label: z.string().min(1), sub: z.string().optional(), highlight: z.boolean().default(false) }).strict())
      .min(1),
    staggerFrames: frame.default(6),
  })
  .strict();

// W33 날짜/콘솔 플립 — JUL 27 SEASON OPENER
const DateFlipSchema = z
  .object({
    type: z.literal("dateFlip"),
    ...promoBase,
    text: z.string().min(1), // 예: "JUL"
    accent: z.string().optional(), // 블루 강조부 (예: "27")
    kicker: z.string().optional(), // 예: "SEASON OPENER"
    flipFrames: frame.default(12),
  })
  .strict();

// W34 티켓 소품 — STANDARD / VIP EXPERIENCE + UPGRADED 스탬프
const TicketPropSchema = z
  .object({
    type: z.literal("ticketProp"),
    ...promoBase,
    tickets: z
      .array(z.object({ tier: z.string().min(1), classLabel: z.string().optional(), highlight: z.boolean().default(false) }).strict())
      .min(1),
    stamp: z.string().optional(), // highlight 티켓 위 스탬프 (예: "UPGRADED")
    slideFrames: frame.default(16),
  })
  .strict();

// ORIG-SEAL 낙관(落款) 스탬프 — 주사 인주 사각 도장 슬램. WINNER 스탬프의 우리식 대체 (P6-B 오리지널).
const SealStampSchema = z
  .object({
    type: z.literal("sealStamp"),
    ...promoBase,
    text: z.string().min(1).max(6), // 인문(印文) — 예: "完", "落款", "檢"
    sub: z.string().optional(), // 하단 소형 라벨 (예: "VERIFIED")
    style: z.enum(["jumun", "baekmun"]).default("jumun"), // 주문인(양각 필+음각 글씨) / 백문인(테두리+양각 글씨)
    slamFrames: frame.default(10),
  })
  .strict();

// W-KT 키네틱 히어로 타이포 — 거대(scaleFrom)로 등장해 정착 크기로 수축 + 글로우.
// 원본 프레임 분석(2026-07-19): KIMI K3 타이틀이 ~1.5×로 등장, 4f(24fps) 수축, 직후 플래시.
const HeroTitleSchema = z
  .object({
    type: z.literal("heroTitle"),
    ...promoBase,
    text: z.string().min(1), // 1행 (화이트) — 예: "SUPER", "KIMI K3"
    accent: z.string().optional(), // 2행 (데이터 블루) — 예: "HEAVYWEIGHT"
    sub: z.string().optional(), // 하단 트래킹 서브라인 — 예: "LARGEST EVER RECORDED"
    underline: z.boolean().default(false), // 타입폭 언더라인 룰
    align: z.enum(["center", "left"]).default("center"),
    scaleFrom: z.number().min(1).max(3).default(1.5), // 등장 스케일
    settleFrames: frame.default(6), // 수축 정착 프레임 (30fps 기준)
  })
  .strict();

// W36 로고 락업 — 아이콘 + 워드마크 + 언더라인 + 가랜드 (화이트 아웃트로 지원)
const LogoLockupSchema = z
  .object({
    type: z.literal("logoLockup"),
    ...promoBase,
    wordmark: z.string().min(1), // 예: "KIMI K3"
    icon: z.string().optional(), // 아이콘 글자 (예: "K")
    sub: z.string().optional(), // 예: "FROM THE DARK SIDE OF THE MOON"
    theme: z.enum(["dark", "white"]).default("dark"),
    bunting: z.boolean().default(false), // 상단 코너 가랜드
  })
  .strict();

export const PromoWidgetSchema = z.discriminatedUnion("type", [
  GaugeSchema,
  StatBarSchema,
  LeaderboardSchema,
  CountUpSchema,
  RankLadderSchema,
  NumberLinePlotSchema,
  PriceTagSchema,
  NodeGraphSchema,
  CurvePlotSchema,
  HeatmapGridSchema,
  ParticleFieldSchema,
  OscilloscopeSchema,
  FlowDiagramSchema,
  StrataDiagramSchema,
  LogCardSchema,
  TerminalSchema,
  ChecklistPanelSchema,
  CalloutPanelSchema,
  PlatformSelectorSchema,
  KickerSchema,
  PillBadgeSchema,
  SportsCalloutSchema,
  MarqueeSchema,
  SplitFlapSchema,
  TimelineScrubberSchema,
  FrameBracketsSchema,
  SubtitleSchema,
  ChevronBadgesSchema,
  DateFlipSchema,
  TicketPropSchema,
  HeroTitleSchema,
  SealStampSchema,
  LogoLockupSchema,
]);

export type PromoWidget = z.infer<typeof PromoWidgetSchema>;
export type GaugeWidget = Extract<PromoWidget, { type: "gauge" }>;
export type StatBarWidget = Extract<PromoWidget, { type: "statBar" }>;
export type LeaderboardWidget = Extract<PromoWidget, { type: "leaderboard" }>;
export type CountUpWidget = Extract<PromoWidget, { type: "countUp" }>;

// 테마 — P6-A. dark-navy(모사 계보 기본) / gamji-gold(감지금니 오리지널 계보)
export const PromoThemeSchema = z.enum(["dark-navy", "gamji-gold"]);

// 갤러리 컴포지션 props — 페이지(패밀리) 단위 위젯 묶음. duration = pages × PAGE_FRAMES.
export const PAGE_FRAMES = 150;

export const PromoGalleryPropsSchema = z
  .object({
    theme: PromoThemeSchema.default("dark-navy"),
    kicker: z.string().default("PROMO WIDGET GALLERY"),
    subtitle: z.string().default("다크 프로모 위젯 자산 카탈로그"),
    pages: z.array(z.array(PromoWidgetSchema)).min(1),
  })
  .strict();

export type PromoGalleryProps = z.infer<typeof PromoGalleryPropsSchema>;

// ---------- P5 연출 계층 (프로덕션 씬 시퀀서) ----------

// 무대 프리셋 — 원본의 환경 레이어 (배경 깊이). 프레임 분석: 컬러 워시(tint)가 순수 다크 위에 얹힌다.
export const StageSchema = z
  .object({
    preset: z
      .enum(["none", "glow", "particle-dust", "grid", "orb", "spotlight", "hanji", "ink-wash"])
      .default("glow"), // hanji·ink-wash = P6-B 감지금니 무대
    tint: z.enum(["none", "blue", "warm"]).default("blue"), // 앰비언트 컬러 워시
    glyph: z.string().optional(), // 저투명 워터마크 글리프 (예: "%")
    intensity: z.number().min(0.2).max(2).default(1), // 무대 강도 배율 (P7 — 기본이 너무 옅다는 피드백)
  })
  .strict();

// 씬 카메라 — 기본 고정(none). 2026-07-19 사용자 피드백: 지속 확대/이동은 조잡함으로 읽힘.
// 움직임의 주체는 값 애니메이션·마퀴·무대다. 카메라는 명시적 opt-in일 때만.
export const CameraSchema = z
  .object({
    move: z.enum(["none", "push-in", "pull-back", "drift-left", "drift-right"]).default("none"),
    amount: z.number().min(0).max(0.25).default(0.04),
  })
  .strict();

// 씬 진입 전환 — 기본 클린 컷(none). 재분석(2026-07-19): 원본의 기본은 하드컷/스윕이고
// 플래시는 서사적 사건(착지·슬램)에만 동반된다. white-flash는 클라이맥스 opt-in.
export const TransitionSchema = z
  .object({
    type: z.enum(["none", "light-sweep", "white-flash", "push-in"]).default("none"),
    durationInFrames: frame.default(10),
  })
  .strict();

export const PromoSceneSchema = z
  .object({
    id: z.string().optional(),
    durationInFrames: z.number().int().positive(), // 씬별 가변 길이 — 모션·내레이션에 맞춘다
    widgets: z.array(PromoWidgetSchema).default([]),
    stage: StageSchema.default({ preset: "glow", tint: "blue", intensity: 1 }),
    camera: CameraSchema.default({ move: "none", amount: 0.04 }),
    transition: TransitionSchema.default({ type: "none", durationInFrames: 10 }), // 이 씬으로 들어오는 전환 (기본 클린 컷)
    kicker: z.string().optional(), // 씬별 키커 (미지정 시 전역)
    subtitle: z.string().optional(), // 씬별 자막 (중앙하단 고정 슬롯)
    flashAt: z.array(frame).default([]), // 씬 내부 플래시 비트 (요소 등장 강조 — 원본 문법)
  })
  .strict();

export const PromoScenePropsSchema = z
  .object({
    theme: PromoThemeSchema.default("dark-navy"),
    kicker: z.string().default(""),
    scenes: z.array(PromoSceneSchema).min(1),
  })
  .strict();

export type PromoScene = z.infer<typeof PromoSceneSchema>;
export type PromoSceneProps = z.infer<typeof PromoScenePropsSchema>;
export type StageSpec = z.infer<typeof StageSchema>;

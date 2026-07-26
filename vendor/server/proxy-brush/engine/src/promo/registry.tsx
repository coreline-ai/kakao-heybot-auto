// 프로모 위젯 registry — type → body 매핑. 기존 src/widgets/registry.tsx와 동형 패턴.
// 스키마에만 추가되고 registry에 빠진 경우 placeholder로 안전하게 렌더한다 (크래시 금지).
import React from "react";
import type { PromoWidget } from "./schema";
import { P } from "./tokens";
import { GaugeBody } from "./bodies/Gauge";
import { StatBarBody } from "./bodies/StatBar";
import { LeaderboardBody } from "./bodies/Leaderboard";
import { CountUpBody } from "./bodies/CountUp";
import { RankLadderBody } from "./bodies/RankLadder";
import { NumberLinePlotBody } from "./bodies/NumberLinePlot";
import { PriceTagBody } from "./bodies/PriceTag";
import { NodeGraphBody } from "./bodies/NodeGraph";
import { CurvePlotBody } from "./bodies/CurvePlot";
import { HeatmapGridBody } from "./bodies/HeatmapGrid";
import { ParticleFieldBody } from "./bodies/ParticleField";
import { OscilloscopeBody } from "./bodies/Oscilloscope";
import { FlowDiagramBody } from "./bodies/FlowDiagram";
import { StrataDiagramBody } from "./bodies/StrataDiagram";
import { LogCardBody } from "./bodies/LogCard";
import { TerminalBody } from "./bodies/Terminal";
import { ChecklistPanelBody } from "./bodies/ChecklistPanel";
import { CalloutPanelBody } from "./bodies/CalloutPanel";
import { PlatformSelectorBody } from "./bodies/PlatformSelector";
import { KickerBody } from "./bodies/Kicker";
import { PillBadgeBody } from "./bodies/PillBadge";
import { SportsCalloutBody } from "./bodies/SportsCallout";
import { MarqueeBody } from "./bodies/Marquee";
import { SplitFlapBody } from "./bodies/SplitFlap";
import { TimelineScrubberBody } from "./bodies/TimelineScrubber";
import { FrameBracketsBody } from "./bodies/FrameBrackets";
import { SubtitleBody } from "./bodies/Subtitle";
import { ChevronBadgesBody } from "./bodies/ChevronBadges";
import { DateFlipBody } from "./bodies/DateFlip";
import { TicketPropBody } from "./bodies/TicketProp";
import { HeroTitleBody } from "./bodies/HeroTitle";
import { SealStampBody } from "./bodies/SealStamp";
import { LogoLockupBody } from "./bodies/LogoLockup";

// 바디는 자신의 좁혀진 타입을 받는다 — registry 값 타입은 공통 시그니처로 캐스트.
export type PromoWidgetBody = React.FC<{ widget: PromoWidget; frame: number }>;
const body = <C,>(c: C) => c as unknown as PromoWidgetBody;

export const PROMO_WIDGET_REGISTRY: Record<PromoWidget["type"], PromoWidgetBody> = {
  gauge: body(GaugeBody),
  statBar: body(StatBarBody),
  leaderboard: body(LeaderboardBody),
  countUp: body(CountUpBody),
  rankLadder: body(RankLadderBody),
  numberLinePlot: body(NumberLinePlotBody),
  priceTag: body(PriceTagBody),
  nodeGraph: body(NodeGraphBody),
  curvePlot: body(CurvePlotBody),
  heatmapGrid: body(HeatmapGridBody),
  particleField: body(ParticleFieldBody),
  oscilloscope: body(OscilloscopeBody),
  flowDiagram: body(FlowDiagramBody),
  strataDiagram: body(StrataDiagramBody),
  logCard: body(LogCardBody),
  terminal: body(TerminalBody),
  checklistPanel: body(ChecklistPanelBody),
  calloutPanel: body(CalloutPanelBody),
  platformSelector: body(PlatformSelectorBody),
  kicker: body(KickerBody),
  pillBadge: body(PillBadgeBody),
  sportsCallout: body(SportsCalloutBody),
  marquee: body(MarqueeBody),
  splitFlap: body(SplitFlapBody),
  timelineScrubber: body(TimelineScrubberBody),
  frameBrackets: body(FrameBracketsBody),
  subtitle: body(SubtitleBody),
  chevronBadges: body(ChevronBadgesBody),
  dateFlip: body(DateFlipBody),
  ticketProp: body(TicketPropBody),
  heroTitle: body(HeroTitleBody),
  sealStamp: body(SealStampBody),
  logoLockup: body(LogoLockupBody),
};

export const PromoPlaceholderBody: PromoWidgetBody = ({ widget }) => (
  <div
    style={{
      height: "100%",
      border: `1.5px dashed ${P.lineStrong}`,
      borderRadius: P.radiusMd,
      display: "grid",
      placeItems: "center",
      color: P.muted,
      fontWeight: 850,
      fontSize: 12,
    }}
  >
    미등록 프로모 위젯: {widget.type}
  </div>
);

export function getPromoWidgetBody(type: string): PromoWidgetBody {
  const found = (PROMO_WIDGET_REGISTRY as Record<string, PromoWidgetBody>)[type];
  if (!found) {
    console.warn(`[promo-widgets] 미등록 타입 "${type}" — placeholder로 렌더합니다`);
    return PromoPlaceholderBody;
  }
  return found;
}

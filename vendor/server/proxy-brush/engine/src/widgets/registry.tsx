// 위젯 registry — type → body 컴포넌트 매핑. 스키마(WidgetSchema)가 타입을 보증하지만,
// 스키마에만 추가되고 registry에 빠진 경우를 대비해 placeholder로 안전하게 렌더한다 (크래시 금지).
import React from "react";
import type { Widget } from "../schema";
import { T } from "./shared";
import { StatBody } from "./bodies/Stat";
import { TextBody } from "./bodies/Text";
import { DonutBody } from "./bodies/Donut";
import { BarsBody } from "./bodies/Bars";
import { HeadlineBody } from "./bodies/Headline";
import { BulletListBody } from "./bodies/BulletList";
import { QuoteTextBody } from "./bodies/QuoteText";
import { CompareBarsBody } from "./bodies/CompareBars";
import { ProcessStepCardBody } from "./bodies/ProcessStepCard";
import { WarningCardBody } from "./bodies/WarningCard";
import { FlowDiagramBody } from "./bodies/FlowDiagram";
import { TimelineStepperBody } from "./bodies/TimelineStepper";
import { DataTableBody } from "./bodies/DataTable";
import { PersonAvatarBody } from "./bodies/PersonAvatar";
import { ChatBubbleBody } from "./bodies/ChatBubble";

// 바디는 자신의 좁혀진 타입을 받는다 — registry 값 타입은 공통 시그니처로 캐스트.
export type WidgetBody = React.FC<{ widget: Widget; accent: string }>;
const body = <C,>(c: C) => c as unknown as WidgetBody;

export const WIDGET_REGISTRY: Record<Widget["type"], WidgetBody> = {
  stat: body(StatBody),
  text: body(TextBody),
  donut: body(DonutBody),
  bars: body(BarsBody),
  Headline: body(HeadlineBody),
  BulletList: body(BulletListBody),
  QuoteText: body(QuoteTextBody),
  CompareBars: body(CompareBarsBody),
  ProcessStepCard: body(ProcessStepCardBody),
  WarningCard: body(WarningCardBody),
  FlowDiagram: body(FlowDiagramBody),
  TimelineStepper: body(TimelineStepperBody),
  DataTable: body(DataTableBody),
  PersonAvatar: body(PersonAvatarBody),
  ChatBubble: body(ChatBubbleBody),
};

export const PlaceholderBody: WidgetBody = ({ widget }) => (
  <div style={{ height: "100%", border: `1.5px dashed ${T.lineStrong}`, borderRadius: 14, display: "grid", placeItems: "center", color: T.muted, fontWeight: 850, fontSize: 12 }}>
    미등록 위젯: {widget.type}
  </div>
);

export function getWidgetBody(type: string): WidgetBody {
  const found = (WIDGET_REGISTRY as Record<string, WidgetBody>)[type];
  if (!found) {
    console.warn(`[widgets] 미등록 타입 "${type}" — placeholder로 렌더합니다`);
    return PlaceholderBody;
  }
  return found;
}

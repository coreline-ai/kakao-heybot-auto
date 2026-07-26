import React from "react";
import type { CardWidget } from "../../schema";
import { T } from "../shared";

// 인용문 — items[0].label(없으면 caption/title)을 따옴표로 감싸 크게.
export const QuoteTextBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ fontSize: 21, lineHeight: 1.22, fontWeight: 900, color: T.ink }}>
    <span style={{ color: accent }}>“</span>
    {widget.items[0]?.label ?? widget.caption ?? widget.title}
    <span style={{ color: accent }}>”</span>
  </div>
);

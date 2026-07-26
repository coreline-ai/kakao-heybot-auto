import React from "react";
import type { CardWidget } from "../../schema";
import { Chip } from "../shared";

// 번호 매긴 단계 칩 — items 순서대로 01, 02, …
export const ProcessStepCardBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "flex", gap: 9 }}>
    {widget.items.map((it, i) => (
      <Chip key={i} label={it.label} value={String(i + 1).padStart(2, "0")} detail={it.detail ?? it.label} accent={accent} tone={it.tone} />
    ))}
  </div>
);

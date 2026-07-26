import React from "react";
import type { CardWidget } from "../../schema";
import { Chip } from "../shared";

// 경고 카드 — 첫 항목은 danger 톤 기본, 나머지는 지정 톤 또는 warn.
export const WarningCardBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "flex", gap: 9, flexWrap: "wrap", alignContent: "start" }}>
    {widget.items.map((it, i) => (
      <Chip key={i} label={it.label} detail={it.detail} value={it.value} accent={accent} tone={it.tone ?? (i === 0 ? "danger" : "warn")} />
    ))}
  </div>
);

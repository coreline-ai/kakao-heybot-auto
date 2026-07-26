import React from "react";
import type { CardWidget } from "../../schema";
import { Node, T } from "../shared";

// 타임라인 스텝 — 수평 기준선 위에 번호 노드, 아래에 label/detail.
export const TimelineStepperBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ position: "relative", height: "100%", display: "flex", justifyContent: "space-around", alignItems: "center" }}>
    <div style={{ position: "absolute", left: 20, right: 20, top: "38%", height: 2, background: T.lineStrong }} />
    {widget.items.map((it, i) => (
      <div key={i} style={{ display: "grid", justifyItems: "center", gap: 5 }}>
        <Node label={String(i + 1).padStart(2, "0")} accent={accent} />
        <span style={{ color: T.ink, fontSize: 11, fontWeight: 850, whiteSpace: "nowrap" }}>{it.label}</span>
      </div>
    ))}
  </div>
);

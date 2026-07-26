import React from "react";
import type { CardWidget } from "../../schema";
import { Node, T } from "../shared";

// 흐름 다이어그램 — items를 노드로, 사이를 → 로 연결. detail은 노드 아래 작은 글씨.
export const FlowDiagramBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 7 }}>
    {widget.items.map((it, i) => (
      <React.Fragment key={i}>
        <div style={{ display: "grid", justifyItems: "center", gap: 4 }}>
          <Node label={it.label.slice(0, 4)} accent={accent} />
          {it.detail && <span style={{ color: T.muted, fontSize: 10, fontWeight: 780 }}>{it.detail}</span>}
        </div>
        {i < widget.items.length - 1 && <span style={{ color: accent, fontWeight: 950, transform: "translateY(-7px)" }}>→</span>}
      </React.Fragment>
    ))}
  </div>
);

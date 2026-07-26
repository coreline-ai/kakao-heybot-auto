import React from "react";
import type { CardWidget } from "../../schema";
import { T } from "../shared";

export const BulletListBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "grid", gap: 7 }}>
    {widget.items.map((it, i) => (
      <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 14, minWidth: 0 }}>
        <span style={{ width: 7, height: 7, borderRadius: 99, background: accent, flex: "0 0 auto", transform: "translateY(-2px)" }} />
        <span style={{ color: T.ink, fontWeight: 820, whiteSpace: "nowrap" }}>{it.label}</span>
        {it.detail && <span style={{ color: T.muted, fontSize: 11.5, fontWeight: 760, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{it.detail}</span>}
      </div>
    ))}
  </div>
);

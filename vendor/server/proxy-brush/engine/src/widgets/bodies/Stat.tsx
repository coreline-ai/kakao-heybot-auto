import React from "react";
import type { Widget } from "../../schema";
import { T } from "../shared";

export const StatBody: React.FC<{ widget: Extract<Widget, { type: "stat" }>; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
    <b style={{ color: accent, fontSize: 42, lineHeight: 1, fontWeight: 950 }}>{widget.value}</b>
    {widget.sub && <span style={{ color: T.muted, fontWeight: 850 }}>{widget.sub}</span>}
  </div>
);

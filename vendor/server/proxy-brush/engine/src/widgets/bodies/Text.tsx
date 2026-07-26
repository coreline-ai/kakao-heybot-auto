import React from "react";
import type { Widget } from "../../schema";
import { T } from "../shared";

export const TextBody: React.FC<{ widget: Extract<Widget, { type: "text" }>; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "grid", gap: 7 }}>
    {widget.lines.map((line, i) => (
      <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", color: T.ink, fontWeight: 820, fontSize: 14, minWidth: 0 }}>
        <span style={{ width: 7, height: 7, borderRadius: 99, background: accent, flex: "0 0 auto" }} />
        <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{line}</span>
      </div>
    ))}
  </div>
);

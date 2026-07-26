import React from "react";
import type { Widget } from "../../schema";

export const BarsBody: React.FC<{ widget: Extract<Widget, { type: "bars" }>; accent: string }> = ({ widget, accent }) => {
  const max = Math.max(...widget.values, 1);
  const peak = widget.values.indexOf(Math.max(...widget.values));
  return (
    <div style={{ height: "100%", display: "flex", alignItems: "end", gap: 10, justifyContent: "center" }}>
      {widget.values.map((v, i) => (
        <span key={i} style={{ width: 22, height: `${(v / max) * 100}%`, borderRadius: 8, background: i === peak ? accent : `${accent}55` }} />
      ))}
    </div>
  );
};

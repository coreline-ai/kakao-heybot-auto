import React from "react";
import type { CardWidget } from "../../schema";
import { T } from "../shared";

// 미니 데이터 테이블 — label | value | detail 3열, 행 구분선.
export const DataTableBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "grid", gap: 5, alignContent: "start" }}>
    {widget.items.map((it, i) => (
      <div key={i} style={{ display: "grid", gridTemplateColumns: "1.2fr .7fr 1fr", gap: 8, fontSize: 11.5, fontWeight: 850, color: T.muted, borderBottom: `1px solid ${T.line}`, paddingBottom: 3, minWidth: 0 }}>
        <span style={{ color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{it.label}</span>
        <span style={{ color: accent }}>{it.value ?? "—"}</span>
        <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{it.detail ?? ""}</span>
      </div>
    ))}
  </div>
);

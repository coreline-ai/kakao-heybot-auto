import React from "react";
import type { CardWidget } from "../../schema";
import { T } from "../shared";

// 인물/대상 아바타 — 첫 항목 label의 첫 글자를 원형 배지로, 옆에 label/detail.
export const PersonAvatarBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => {
  const first = widget.items[0];
  const initial = (first?.label ?? widget.title).slice(0, 2);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <div style={{ width: 54, height: 54, borderRadius: 999, background: `${accent}20`, border: `1.5px solid ${accent}`, display: "grid", placeItems: "center", color: accent, fontWeight: 950, fontSize: 16, flex: "0 0 auto" }}>
        {initial}
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 900, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{first?.label ?? widget.title}</div>
        {first?.detail && <div style={{ color: T.muted, fontSize: 11.5, fontWeight: 780, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{first.detail}</div>}
      </div>
    </div>
  );
};

import React from "react";
import type { CardWidget } from "../../schema";
import { T } from "../shared";

// 큰 헤드라인 텍스트 — items의 label을 줄로 쌓고 첫 줄만 accent 강조. items 없으면 caption을 크게.
export const HeadlineBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => {
  const lines = widget.items.length ? widget.items.map((it) => it.label) : [widget.caption ?? widget.title];
  return (
    <div style={{ fontSize: 28, lineHeight: 1.08, fontWeight: 960, letterSpacing: "-1px", color: T.ink }}>
      {lines.map((line, i) => (
        <div key={i} style={{ color: i === 0 ? accent : T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{line}</div>
      ))}
    </div>
  );
};

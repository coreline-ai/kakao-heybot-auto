import React from "react";
import type { CardWidget } from "../../schema";
import { T } from "../shared";

// 대화 말풍선 — items를 좌/우 교대로 배치 (짝수 인덱스 = 왼쪽 흰 풍선, 홀수 = 오른쪽 accent 풍선).
export const ChatBubbleBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => (
  <div style={{ display: "grid", gap: 7, alignContent: "start" }}>
    {widget.items.map((it, i) => {
      const right = i % 2 === 1;
      return (
        <div
          key={i}
          style={{
            justifySelf: right ? "end" : "start",
            maxWidth: "82%",
            padding: "7px 11px",
            borderRadius: 16,
            fontSize: 12.5,
            fontWeight: 820,
            color: T.ink,
            background: right ? `${accent}1f` : "rgba(255,255,255,.66)",
            border: `1px solid ${right ? `${accent}55` : T.line}`,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {it.label}
          {it.detail && <span style={{ color: T.muted, marginLeft: 6, fontSize: 10.5 }}>{it.detail}</span>}
        </div>
      );
    })}
  </div>
);

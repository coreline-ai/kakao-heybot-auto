import React from "react";
import type { CardWidget } from "../../schema";
import { HBar, T } from "../shared";

// 항목별 수평 바 비교 — item.value가 숫자면 그대로(0~100), 없으면 균등 증가 배치. 마지막 항목만 accent.
export const CompareBarsBody: React.FC<{ widget: CardWidget; accent: string }> = ({ widget, accent }) => {
  const items = widget.items;
  return (
    <div style={{ display: "grid", gap: 8, alignContent: "start" }}>
      {items.map((it, i) => {
        const v = typeof it.value === "number" ? it.value : ((i + 1) / items.length) * 88;
        return <HBar key={i} label={it.label} value={v} accent={i === items.length - 1 ? accent : T.faint} />;
      })}
    </div>
  );
};

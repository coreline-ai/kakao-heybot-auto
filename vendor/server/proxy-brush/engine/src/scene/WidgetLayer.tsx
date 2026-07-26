// 위젯 레이어 (z 22) — scene.widgets를 CardShell + registry 바디로 렌더.
import React from "react";
import type { Widget } from "../schema";
import { getWidgetBody } from "../widgets/registry";
import { CardShell, pickAccent } from "../widgets/shared";

export const WidgetLayer: React.FC<{ frame: number; widgets: Widget[] }> = ({ frame, widgets }) => {
  if (!widgets.length) return null;
  return (
    <div style={{ position: "absolute", inset: 0, zIndex: 22, pointerEvents: "none" }}>
      {widgets.map((w, i) => {
        const Body = getWidgetBody(w.type);
        return (
          <CardShell key={`${w.type}-${i}`} widget={w} frame={frame} index={i}>
            <Body widget={w} accent={pickAccent(w, i)} />
          </CardShell>
        );
      })}
    </div>
  );
};

import React from "react";
import type { Widget } from "../../schema";
import { Ring } from "../shared";

export const DonutBody: React.FC<{ widget: Extract<Widget, { type: "donut" }>; accent: string }> = ({ widget, accent }) => (
  <Ring pct={widget.pct} accent={accent} />
);

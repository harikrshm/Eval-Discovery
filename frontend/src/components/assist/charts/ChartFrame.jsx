import { cloneElement } from "react";
import { ResponsiveContainer } from "recharts";

// Responsive in the app; a fixed `width` bypasses ResponsiveContainer (jsdom has no layout, so it
// would measure 0 x 0). `chart` is one Recharts chart element.
export default function ChartFrame({ width = null, height, chart }) {
  if (width) return cloneElement(chart, { width, height });
  return (
    <ResponsiveContainer width="100%" height={height}>
      {chart}
    </ResponsiveContainer>
  );
}

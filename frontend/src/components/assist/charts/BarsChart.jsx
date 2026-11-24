import { useRef, useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Rectangle, Tooltip, XAxis, YAxis } from "recharts";
import { NA, isNumber } from "../format";
import ChartFrame from "./ChartFrame";
import ChartLegend from "./ChartLegend";
import { AXIS_LINE, FOCUS, GRID, TICK_TEXT, seriesColor } from "./palette";

const INK = "#111111";

function plain(value) {
  return isNumber(value) ? String(Math.round(value * 100) / 100) : NA;
}

const RING_WIDTH = 3;

// A bar that is a button: focusable, Enter/Space/click select its category. The focus ring is a
// thick FOCUS-coloured stroke on the bar itself, drawn from state (no CSS outline on SVG). Like
// :focus-visible, it shows for keyboard focus only: focus that follows a mouse press shows no ring.
function SelectableBar({ props, category, label, radius, onSelect }) {
  const [focused, setFocused] = useState(false);
  const pressed = useRef(false);
  const { x, y, width, height, fill } = props;
  if (!isNumber(x) || !isNumber(y) || !(width > 0) || !(height > 0)) return null;
  const choose = () => onSelect(category);
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={label}
      onClick={choose}
      onPointerDown={() => {
        pressed.current = true;
      }}
      onFocus={() => setFocused(!pressed.current)}
      onBlur={() => {
        pressed.current = false;
        setFocused(false);
      }}
      onKeyDown={(event) => {
        setFocused(true);
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      }}
      className="cursor-pointer"
    >
      <Rectangle
        x={x}
        y={y}
        width={width}
        height={height}
        fill={fill}
        radius={radius}
        stroke={focused ? FOCUS : "none"}
        strokeWidth={focused ? RING_WIDTH : 0}
      />
    </g>
  );
}

// Bars with a value label on each. `layout="horizontal"` (default) draws horizontal bars with the
// category names down the left, which is the readable choice for names; `"vertical"` draws columns.
// `grouped` (default) puts series side by side, `grouped={false}` stacks them (no value labels
// then: the axis carries the values). A null value is no bar; the category says "n/a".
// `onSelect(category)` makes each bar a focusable button. WARNING: a chart with `onSelect` must sit
// in `<ChartCard interactive>`; the default `role="img"` wrapper makes the bar buttons invisible to
// assistive technology. `width` fixes the size (tests).
export default function BarsChart({
  data,
  categoryKey,
  series,
  layout = "horizontal",
  grouped = true,
  onSelect = null,
  valueFormat = plain,
  height = null,
  width = null,
}) {
  const horizontalBars = layout !== "vertical";
  const bars = series.map((s, i) => ({ ...s, color: seriesColor(i, s.color) }));
  const rows = data.map((row) => {
    const out = { [categoryKey]: row[categoryKey] };
    for (const s of series) out[s.key] = isNumber(row[s.key]) ? row[s.key] : null;
    return out;
  });
  const missing = new Set(
    rows.filter((row) => series.every((s) => row[s.key] === null)).map((row) => String(row[categoryKey])),
  );
  const categoryLabel = (value) => (missing.has(String(value)) ? `${value} (n/a)` : String(value));
  const chartHeight = height ?? (horizontalBars ? Math.max(120, rows.length * (bars.length > 1 && grouped ? 28 * bars.length : 32) + 32) : 240);
  const stacked = !grouped && bars.length > 1;
  const radius = stacked ? 0 : horizontalBars ? [0, 4, 4, 0] : [4, 4, 0, 0];

  const categoryAxis = horizontalBars ? (
    <YAxis
      type="category"
      dataKey={categoryKey}
      width={120}
      tickFormatter={categoryLabel}
      tick={{ fill: TICK_TEXT, fontSize: 11 }}
      stroke={AXIS_LINE}
      tickLine={false}
    />
  ) : (
    <XAxis
      type="category"
      dataKey={categoryKey}
      tickFormatter={categoryLabel}
      tick={{ fill: TICK_TEXT, fontSize: 11 }}
      stroke={AXIS_LINE}
      tickLine={false}
    />
  );
  const valueAxis = horizontalBars ? (
    <XAxis type="number" tickFormatter={(v) => valueFormat(v)} tick={{ fill: TICK_TEXT, fontSize: 11 }} stroke={AXIS_LINE} tickLine={false} />
  ) : (
    <YAxis type="number" width={48} tickFormatter={(v) => valueFormat(v)} tick={{ fill: TICK_TEXT, fontSize: 11 }} stroke={AXIS_LINE} tickLine={false} axisLine={false} />
  );

  const chart = (
    <BarChart
      data={rows}
      layout={horizontalBars ? "vertical" : "horizontal"}
      margin={{ top: 8, right: horizontalBars ? 44 : 12, bottom: 4, left: 0 }}
      accessibilityLayer
    >
      <CartesianGrid stroke={GRID} horizontal={!horizontalBars} vertical={horizontalBars} />
      {categoryAxis}
      {valueAxis}
      <Tooltip
        formatter={(value, name) => [isNumber(value) ? valueFormat(value) : NA, name]}
        labelFormatter={categoryLabel}
        cursor={{ fill: GRID, fillOpacity: 0.4 }}
        contentStyle={{ fontSize: 12 }}
        isAnimationActive={false}
      />
      {bars.map((bar) => (
        <Bar
          key={bar.key}
          dataKey={bar.key}
          name={bar.label}
          fill={bar.color}
          stackId={stacked ? "stack" : undefined}
          radius={radius}
          maxBarSize={22}
          isAnimationActive={false}
          shape={
            onSelect
              ? (props) => {
                  const category = props.payload?.[categoryKey];
                  const value = props.payload?.[bar.key];
                  const label = `${category}${bars.length > 1 ? `, ${bar.label}` : ""}: ${valueFormat(value)}`;
                  return (
                    <SelectableBar
                      key={`${category}-${bar.key}`}
                      props={props}
                      category={category}
                      label={label}
                      radius={radius}
                      onSelect={onSelect}
                    />
                  );
                }
              : undefined
          }
        >
          {stacked ? null : (
            <LabelList
              dataKey={bar.key}
              position={horizontalBars ? "right" : "top"}
              formatter={(v) => (isNumber(v) ? valueFormat(v) : "")}
              fill={INK}
              fontSize={11}
            />
          )}
        </Bar>
      ))}
    </BarChart>
  );

  const legend = bars.length > 1 ? bars.map((b) => ({ label: b.label, color: b.color, shape: "bar" })) : [];

  return (
    <div>
      <ChartFrame width={width} height={chartHeight} chart={chart} />
      <ChartLegend items={legend} />
    </div>
  );
}

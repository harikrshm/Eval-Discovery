import { CartesianGrid, Dot, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from "recharts";
import { NA, isNumber } from "../format";
import ChartFrame from "./ChartFrame";
import ChartLegend from "./ChartLegend";
import { AXIS_LINE, GRID, MARKER, SURFACE, TICK_TEXT, seriesColor } from "./palette";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MAX_TICKS = 6;
const DOTS_UNTIL = 14;
const HAS_ZONE = /(?:[zZ]|[+-]\d\d:?\d\d)$/;

// Timestamps from the backend are naive UTC ("2026-11-20T10:00:00", sometimes with a Z). The
// browser would read a bare one as local time, so a missing zone is read as UTC.
function toMillis(value) {
  if (isNumber(value)) return value;
  if (typeof value !== "string" || !value) return NaN;
  const text = /T\d/.test(value) && !HAS_ZONE.test(value) ? `${value}Z` : value;
  return Date.parse(text);
}

const two = (n) => String(n).padStart(2, "0");

const dayOf = (ms) => Math.floor(ms / 86400000);
const clock = (d) => `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;
const dateOf = (d) => `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;

// Hourly buckets are counted back from the request time, so they do not start on the hour:
// the label is the real UTC HH:MM. The date is added only to the first tick of a new UTC day.
function tickLabel(ms, index, ticks, bucket) {
  const d = new Date(ms);
  if (bucket !== "hour") return dateOf(d);
  const newDay = index > 0 && dayOf(ticks[index - 1]) !== dayOf(ms);
  return newDay ? `${dateOf(d)} ${clock(d)}` : clock(d);
}

function fullLabel(ms, bucket) {
  const d = new Date(ms);
  return bucket === "hour" ? `${dateOf(d)} ${clock(d)} UTC` : dateOf(d);
}

// Every bucket start is a real data point; thin them to a readable few.
function pickTicks(times) {
  if (times.length <= MAX_TICKS) return times;
  const stride = Math.ceil((times.length - 1) / (MAX_TICKS - 1));
  const picked = times.filter((_, i) => i % stride === 0);
  return picked;
}

function plain(value) {
  return isNumber(value) ? String(Math.round(value * 100) / 100) : NA;
}

// A line chart over time. `data` rows carry an ISO timestamp under `xKey` and one number per series
// key; a missing number (null/undefined/NaN) is a gap in the line, never zero. `bucket` is
// "hour" or "day" and decides how ticks read. `markers` are dashed vertical lines at a time.
// `width` fixes the size (tests); leave it out to fill the container. Pass at most 4 series: the
// palette's contrast does not support more (see palette.js).
export default function TrendChart({
  data,
  xKey,
  series,
  bucket = "day",
  markers = [],
  yFormat = plain,
  height = 220,
  width = null,
}) {
  const rows = data
    .map((row) => {
      const out = { __t: toMillis(row[xKey]) };
      for (const s of series) out[s.key] = isNumber(row[s.key]) ? row[s.key] : null;
      return out;
    })
    .filter((row) => Number.isFinite(row.__t))
    .sort((a, b) => a.__t - b.__t);

  const times = rows.map((r) => r.__t);
  const ticks = pickTicks(times);
  const lines = series.map((s, i) => ({ ...s, color: seriesColor(i, s.color) }));
  const drawn = markers
    .map((m) => ({ ...m, ms: toMillis(m.at), text: m.demo ? `${m.label} (demo)` : m.label }))
    .filter((m) => Number.isFinite(m.ms));
  const bounded = (value) => (value === null || value === undefined ? NA : yFormat(value));

  const chart = (
    <LineChart data={rows} margin={{ top: drawn.length ? 20 : 8, right: 12, bottom: 4, left: 0 }} accessibilityLayer>
      <CartesianGrid stroke={GRID} vertical={false} />
      <XAxis
        dataKey="__t"
        type="number"
        scale="time"
        domain={["dataMin", "dataMax"]}
        ticks={ticks}
        tickFormatter={(ms, index) => tickLabel(ms, index, ticks, bucket)}
        tick={{ fill: TICK_TEXT, fontSize: 11 }}
        stroke={AXIS_LINE}
        tickLine={false}
      />
      <YAxis
        domain={[0, "auto"]}
        width={48}
        tickFormatter={(v) => yFormat(v)}
        tick={{ fill: TICK_TEXT, fontSize: 11 }}
        stroke={AXIS_LINE}
        tickLine={false}
        axisLine={false}
      />
      <Tooltip
        labelFormatter={(ms) => fullLabel(ms, bucket)}
        formatter={(value, name) => [bounded(value), name]}
        cursor={{ stroke: AXIS_LINE }}
        contentStyle={{ fontSize: 12 }}
        isAnimationActive={false}
      />
      {drawn.map((m) => (
        <ReferenceLine
          key={`${m.at}-${m.text}`}
          x={m.ms}
          stroke={MARKER}
          strokeDasharray="4 4"
          label={{ value: m.text, position: "top", fill: MARKER, fontSize: 10 }}
        />
      ))}
      {lines.map((line) => (
        <Line
          key={line.key}
          type="linear"
          dataKey={line.key}
          name={line.label}
          stroke={line.color}
          strokeWidth={2}
          connectNulls={false}
          isAnimationActive={false}
          dot={(props) => {
            const { cx, cy, index, key } = props;
            if (!isNumber(cx) || !isNumber(cy)) return null;
            // Many points: dots only where a point stands alone (no neighbour to draw a line to).
            const alone = rows[index - 1]?.[line.key] == null && rows[index + 1]?.[line.key] == null;
            if (rows.length > DOTS_UNTIL && !alone) return null;
            return <Dot key={key} cx={cx} cy={cy} r={4} fill={line.color} stroke={SURFACE} strokeWidth={2} />;
          }}
          activeDot={{ r: 5, stroke: SURFACE, strokeWidth: 2 }}
        />
      ))}
    </LineChart>
  );

  const legend = [
    ...lines.map((l) => ({ label: l.label, color: l.color, shape: "line" })),
    ...drawn.map((m) => ({ label: m.text, color: MARKER, shape: "marker" })),
  ];

  return (
    <div>
      <ChartFrame width={width} height={height} chart={chart} />
      <ChartLegend items={legend} />
    </div>
  );
}

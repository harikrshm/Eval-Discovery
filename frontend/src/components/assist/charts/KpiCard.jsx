import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import { Line, LineChart, YAxis } from "recharts";
import { NA, isNumber } from "../format";
import { GOOD_TEXT, SERIES, STATUS } from "./palette";

const SPARK_WIDTH = 96;
const SPARK_HEIGHT = 28;

function shown(value) {
  if (isNumber(value)) return String(value);
  if (typeof value === "string" && value) return value;
  return NA;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

// Decorative: the number beside it carries the information. `null` points are gaps.
function Sparkline({ points }) {
  const data = points.map((v, i) => ({ i, v: isNumber(v) ? v : null }));
  if (!data.some((d) => d.v !== null)) return null;
  return (
    <div data-sparkline aria-hidden="true" className="shrink-0">
      <LineChart width={SPARK_WIDTH} height={SPARK_HEIGHT} data={data} margin={{ top: 3, right: 3, bottom: 3, left: 3 }}>
        <YAxis hide domain={["dataMin", "dataMax"]} />
        <Line
          type="linear"
          dataKey="v"
          stroke={SERIES[0]}
          strokeWidth={2}
          dot={false}
          activeDot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
      </LineChart>
    </div>
  );
}

// Direction is in words ("up 3 pts") and an arrow; whether it is good news is also in words
// ("better"/"worse"), with colour only as a reinforcement.
function Delta({ delta, unit, good }) {
  const amount = round1(Math.abs(delta));
  const direction = amount === 0 ? "flat" : delta > 0 ? "up" : "down";
  const text = direction === "flat" ? "no change" : `${direction} ${amount} ${unit}`;
  const judged = direction === "flat" || !good ? null : direction === good ? "better" : "worse";
  const Icon = direction === "flat" ? Minus : direction === "up" ? ArrowUp : ArrowDown;
  const colour = judged === "better" ? GOOD_TEXT : judged === "worse" ? STATUS.bad : STATUS.neutral;

  return (
    <p data-delta={direction} className="mt-1 flex items-center gap-1 text-xs text-foreground">
      <Icon aria-hidden="true" className="h-3.5 w-3.5 shrink-0" style={{ color: colour }} />
      <span>{text}</span>
      {judged ? <span className="text-muted-foreground">({judged})</span> : null}
    </p>
  );
}

// One headline number. `delta` is a signed change in `deltaUnit` (default "pts"); `deltaGood` says
// which direction is good news. A missing value shows "n/a" (and no delta, which would be
// meaningless); a missing delta shows no delta line.
export default function KpiCard({ label, value, unit = null, delta = null, deltaUnit = "pts", deltaGood = null, sparkline = null }) {
  const known = isNumber(value) || (typeof value === "string" && value !== "");
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className="min-w-0 text-2xl font-semibold text-foreground">
          {shown(value)}
          {known && unit ? <span className="ml-1 text-sm font-normal text-muted-foreground">{unit}</span> : null}
        </p>
        {Array.isArray(sparkline) && sparkline.length > 1 ? <Sparkline points={sparkline} /> : null}
      </div>
      {known && isNumber(delta) ? <Delta delta={delta} unit={deltaUnit} good={deltaGood} /> : null}
    </div>
  );
}

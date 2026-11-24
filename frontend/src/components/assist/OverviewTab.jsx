import { assistOverview } from "../../api";
import { useConsoleQuery } from "../../hooks/useConsoleQuery";
import { Button } from "../ui/button";
import AlertStrip from "./AlertStrip";
import LoopStepper from "./LoopStepper";
import ChartCard from "./charts/ChartCard";
import KpiCard from "./charts/KpiCard";
import PanelState from "./charts/PanelState";
import RefreshFrame from "./RefreshFrame";
import TrendChart from "./charts/TrendChart";
import { isNumber, utcStamp } from "./format";

const GRADE_SERIES = [{ key: "avg_grade", label: "Average grade (1 to 5)" }];

// Nothing to show when no count is above zero, nothing was judged, and no alert is active.
function isEmptyOverview(data) {
  if (!data) return true;
  const hasKpi = (data.kpis ?? []).some(
    (k) => k.unit !== "version" && isNumber(k.value) && (k.unit === "percent" || k.value > 0)
  );
  const hasTrend = (data.quality_trend ?? []).some((p) => isNumber(p.avg_grade));
  return !hasKpi && !hasTrend && !(data.alerts ?? []).length;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

// A count's change is relative (percent); a percent's change is in points. Both are against the
// previous period of the same length, and the backend sends no change for the range "all".
function kpiProps(kpi) {
  const base = { label: kpi.label, deltaGood: kpi.delta_good, sparkline: kpi.sparkline ?? [] };
  if (kpi.unit === "percent") {
    return {
      ...base,
      value: isNumber(kpi.value) ? round1(kpi.value) : null,
      unit: "%",
      delta: kpi.delta,
      deltaUnit: "pts vs previous period",
    };
  }
  if (kpi.unit === "count") {
    return { ...base, value: kpi.value, delta: kpi.delta, deltaUnit: "% vs previous period" };
  }
  return { ...base, value: kpi.value };
}

// The marker label already says "(demo)" for generated data; the chart adds it from `demo`.
function chartMarkers(markers) {
  return (markers ?? []).map((m) => ({
    at: m.at,
    label: String(m.label ?? `v${m.version}`).replace(/ \(demo\)$/, ""),
    demo: m.source === "demo",
  }));
}

function QualityTrend({ trend, markers, bucket }) {
  const hasPoints = trend.some((p) => isNumber(p.avg_grade));
  if (!hasPoints) {
    return (
      <section aria-labelledby="overview-quality-title" className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h3 id="overview-quality-title" className="text-sm font-semibold text-foreground">
          Quality over time
        </h3>
        <p className="text-sm text-muted-foreground">No judged conversations in this range yet.</p>
      </section>
    );
  }
  return (
    <ChartCard
      title="Quality over time"
      subtitle="Average grade of auto-judged conversations, 1 (worst) to 5 (best). Times are UTC."
      tableColumns={["Time (UTC)", "Average grade"]}
      tableRows={trend.map((p) => [utcStamp(p.t), isNumber(p.avg_grade) ? p.avg_grade : null])}
    >
      <TrendChart data={trend} xKey="t" series={GRADE_SERIES} bucket={bucket} markers={chartMarkers(markers)} />
    </ChartCard>
  );
}

// The Overview tab: the alerts that need attention, four headline numbers, quality over time with the
// prompt versions marked, and where the improvement loop stands. `onOpenTab(tabId, anchorId?)` is
// how an alert or the loop sends the builder to another tab.
export default function OverviewTab({ filters, reloadKey, onOpenTab }) {
  const query = useConsoleQuery(assistOverview, filters, reloadKey, { isEmpty: isEmptyOverview });
  const { state, data } = query;
  const shown = state === "ready" ? data : null;

  return (
    <RefreshFrame refreshing={query.refreshing}>
      <PanelState state={state} what="the overview" error={query.error} onRetry={query.retry}>
        {shown ? (
          <div className="space-y-6">
            <AlertStrip alerts={shown.alerts} onOpen={(alert) => onOpenTab?.(alert.tab, alert.anchor)} />

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {(shown.kpis ?? []).map((kpi) => (
                <div key={kpi.id} data-kpi={kpi.id} className="min-w-0">
                  <KpiCard {...kpiProps(kpi)} />
                </div>
              ))}
            </div>

            <QualityTrend
              trend={shown.quality_trend ?? []}
              markers={shown.version_markers}
              bucket={filters?.range === "24h" ? "hour" : "day"}
            />

            <section aria-labelledby="overview-loop-title" className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 id="overview-loop-title" className="text-sm font-semibold text-foreground">
                  Improvement loop
                </h3>
                <Button type="button" variant="outline" size="sm" onClick={() => onOpenTab?.("evolution")}>
                  Open Evolution
                </Button>
              </div>
              <LoopStepper steps={shown.loop?.steps} />
              {shown.loop?.cycle_note ? <p className="text-xs text-muted-foreground">{shown.loop.cycle_note}</p> : null}
            </section>
          </div>
        ) : null}
      </PanelState>
    </RefreshFrame>
  );
}

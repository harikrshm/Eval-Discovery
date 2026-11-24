import { useRef, useState } from "react";
import { assistFlagged, assistMetrics, assistTrends } from "../../api";
import { useConsoleQuery } from "../../hooks/useConsoleQuery";
import BarsChart from "./charts/BarsChart";
import ChartCard from "./charts/ChartCard";
import KpiCard from "./charts/KpiCard";
import PanelState from "./charts/PanelState";
import TrendChart from "./charts/TrendChart";
import FlaggedList from "./FlaggedList";
import InsightsBrief from "./InsightsBrief";
import RefreshFrame from "./RefreshFrame";
import VersionComparison from "./VersionComparison";
import { chartMarkers } from "./chartMarkers";
import { NA, humanize, isNumber, pct, per100, utcStamp } from "./format";

const FLAGGED_LIMIT = 50;
const NO_FILTER = { intent: null, section: null };
const SELECT_HINT = "Select a bar to list the flagged conversations behind it.";
const FLAGGED_NOTE = `The ${FLAGGED_LIMIT} newest. The source filter applies; the time range and version do not.`;
// The flagged list uses the console's source only; a fixed range and no version keep it from refetching
// when only those change.
const FLAGGED_QUERY_FILTERS = { range: "all", version: null };

const DEFINITIONS = [
  "Coverage = share of non-bounce page views where the section was visible for at least 3 seconds.",
  "Help demand = questions about a section per 100 non-bounce views in which that section was visible at all. It can exceed 100.",
  'Thumbs-down = "down" ratings only.',
  "Refusals = legal-opinion questions.",
  "Widget usage is NOT measured.",
];

const TREND_SERIES = [
  { key: "page_views", label: "Page views" },
  { key: "questions", label: "Questions" },
];
const COVERAGE_SERIES = [{ key: "value", label: "Coverage" }];
const HELP_SERIES = [{ key: "value", label: "Questions per 100 views" }];
const MIX_SERIES = [{ key: "value", label: "Share of questions" }];
const STRUGGLE_SERIES = [
  { key: "reask", label: "Re-ask rate" },
  { key: "thumbs", label: "Thumbs-down rate" },
];

const THUMBS_NOTE =
  "Since widget v2 there is one rating per conversation (after the 3rd answered question), so this rate is not comparable with earlier data.";

const never = () => false;

function round1(value) {
  return Math.round(value * 10) / 10;
}

function total(counts, key) {
  return isNumber(counts?.[key]) ? counts[key] : 0;
}

// A rate with a zero (or missing) denominator is unknown, never 0.
function rate(numerator, denominator) {
  return isNumber(numerator) && isNumber(denominator) && denominator > 0 ? numerator / denominator : null;
}

// Change of a count against the previous period, in percent; unknown without a previous count above zero.
function relativeChange(now, before) {
  return isNumber(now) && isNumber(before) && before > 0 ? ((now - before) / before) * 100 : null;
}

// Change of a rate in percentage points; unknown unless both periods have a rate.
function pointChange(now, before) {
  return isNumber(now) && isNumber(before) ? (now - before) * 100 : null;
}

function isEmptyTrends(data) {
  return !data || total(data.totals, "page_views") + total(data.totals, "questions") === 0;
}

function isEmptyMetrics(data) {
  const totals = data?.adoption?.totals;
  return total(totals, "page_views") + total(totals, "questions") === 0;
}

// The four headline numbers, from the trends payload alone: whole-range totals against the totals of
// the previous period of the same length (null for the range "all", which then shows no change).
function kpiCards(trends) {
  const now = trends.totals ?? {};
  const before = trends.previous ?? null;
  const points = trends.points ?? [];
  const rateOf = (counts, key) => rate(counts?.[key], counts?.questions);
  const spark = (key) => points.map((p) => p[key]);
  const sparkRate = (key) => points.map((p) => rate(p[key], p.questions));
  const asPercent = (value) => (isNumber(value) ? round1(value * 100) : null);

  return [
    {
      id: "page_views",
      props: {
        label: "Page views",
        value: now.page_views ?? null,
        delta: before ? relativeChange(now.page_views, before.page_views) : null,
        deltaUnit: "% vs previous period",
        deltaGood: "up",
        sparkline: spark("page_views"),
      },
    },
    {
      id: "questions",
      props: {
        label: "Questions",
        value: now.questions ?? null,
        delta: before ? relativeChange(now.questions, before.questions) : null,
        deltaUnit: "% vs previous period",
        deltaGood: "up",
        sparkline: spark("questions"),
      },
    },
    {
      id: "reask_rate",
      props: {
        label: "Re-ask rate",
        value: asPercent(rateOf(now, "reasked")),
        unit: "%",
        delta: before ? pointChange(rateOf(now, "reasked"), rateOf(before, "reasked")) : null,
        deltaUnit: "pts vs previous period",
        deltaGood: "down",
        sparkline: sparkRate("reasked"),
      },
    },
    {
      id: "thumbs_down_rate",
      note: THUMBS_NOTE,
      props: {
        label: "Thumbs-down rate",
        value: asPercent(rateOf(now, "rated_down")),
        unit: "%",
        delta: before ? pointChange(rateOf(now, "rated_down"), rateOf(before, "rated_down")) : null,
        deltaUnit: "pts vs previous period",
        deltaGood: "down",
        sparkline: sparkRate("rated_down"),
      },
    },
  ];
}

function UsageTrend({ trends }) {
  const points = trends.points ?? [];
  return (
    <ChartCard
      title="Page views and questions over time"
      subtitle="Counts per bucket; dashed lines mark when a prompt version went live. Times are UTC."
      tableColumns={["Time (UTC)", "Page views", "Questions"]}
      tableRows={points.map((p) => [utcStamp(p.t), p.page_views ?? null, p.questions ?? null])}
    >
      <TrendChart
        data={points}
        xKey="t"
        series={TREND_SERIES}
        bucket={trends.bucket === "hour" ? "hour" : "day"}
        markers={chartMarkers(trends.version_markers)}
      />
    </ChartCard>
  );
}

// A bar chart whose bars are the sections or tasks of one metric. Each bar opens the flagged list for
// its category. The category shown is the readable name; `onPick` receives the real key.
function PickerCard({ id, title, subtitle, kind, rows, series, tableColumns, tableRow, valueFormat, onPick }) {
  if (rows.length === 0) {
    return (
      <section aria-label={title} id={id} className="space-y-2 rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <p className="text-sm text-muted-foreground">Not enough data yet.</p>
      </section>
    );
  }
  const data = rows.map((row) => ({ ...row, name: humanize(row.key) }));
  return (
    <ChartCard
      id={id}
      title={title}
      subtitle={`${subtitle} ${SELECT_HINT}`}
      tableColumns={tableColumns}
      tableRows={data.map((row) => [row.name, ...tableRow(row)])}
      interactive
    >
      <BarsChart
        data={data}
        categoryKey="name"
        series={series}
        valueFormat={valueFormat}
        onSelect={(name) => onPick(kind, data.find((row) => row.name === name)?.key)}
      />
    </ChartCard>
  );
}

function Section({ id, title, children }) {
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h3 id={id} className="text-sm font-semibold text-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

function MetricPanels({ adoption, onPick }) {
  const coverage = adoption.feature_coverage ?? {};
  const views = adoption.views_by_section ?? {};
  const helpDemand = adoption.help_demand ?? {};
  const sections = Object.keys(coverage);
  const withoutViews = Object.entries(adoption.questions_without_views ?? {});
  const taskMix = Object.entries(adoption.task_mix ?? {});
  const struggle = adoption.struggle ?? {};
  const reask = struggle.reask_rate_by_intent ?? {};
  const thumbsDown = struggle.thumbs_down_rate_by_intent ?? {};

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="min-w-0">
          <PickerCard
            title="Feature coverage by section"
            subtitle="Share of non-bounce page views where the section was visible for at least 3 seconds."
            kind="section"
            rows={sections.map((key) => ({ key, value: coverage[key] }))}
            series={COVERAGE_SERIES}
            tableColumns={["Section", "Coverage"]}
            tableRow={(row) => [pct(row.value)]}
            valueFormat={pct}
            onPick={onPick}
          />
        </div>
        <div className="min-w-0">
          <PickerCard
            title="Help demand by section"
            subtitle="Questions about a section per 100 non-bounce views in which it was visible. It can exceed 100."
            kind="section"
            rows={sections.map((key) => ({ key, value: helpDemand[key], views: views[key] ?? 0 }))}
            series={HELP_SERIES}
            tableColumns={["Section", "Views", "Questions per 100 views"]}
            tableRow={(row) => [row.views, per100(row.value)]}
            valueFormat={per100}
            onPick={onPick}
          />
        </div>
        <div className="min-w-0">
          <PickerCard
            title="Task mix"
            subtitle="Share of questions by task."
            kind="intent"
            rows={taskMix.map(([key, row]) => ({ key, value: row?.share ?? null, count: row?.count ?? 0 }))}
            series={MIX_SERIES}
            tableColumns={["Task", "Questions", "Share"]}
            tableRow={(row) => [row.count, pct(row.value)]}
            valueFormat={pct}
            onPick={onPick}
          />
        </div>
        <div className="min-w-0">
          <PickerCard
            id="adoption-reasks"
            title="Re-ask and thumbs-down rate by task"
            subtitle="Share of a task's questions that were asked again, and that were rated down."
            kind="intent"
            rows={Object.keys(reask).map((key) => ({ key, reask: reask[key], thumbs: thumbsDown[key] ?? null }))}
            series={STRUGGLE_SERIES}
            tableColumns={["Task", "Re-ask rate", "Thumbs-down rate"]}
            tableRow={(row) => [pct(row.reask), pct(row.thumbs)]}
            valueFormat={pct}
            onPick={onPick}
          />
        </div>
      </div>

      {withoutViews.length > 0 && (
        <p className="text-sm text-muted-foreground">
          Questions about sections with no recorded views:{" "}
          {withoutViews.map(([name, count]) => `${humanize(name)} (${count})`).join(", ")}
        </p>
      )}

      <Section id="struggle-heading" title="Where people struggle">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          <dt className="text-muted-foreground">Navigation help questions</dt>
          <dd className="tabular-nums">{struggle.navigation_help_count ?? NA}</dd>
          <dt className="text-muted-foreground">Refusals</dt>
          <dd className="tabular-nums">{struggle.refusal_count ?? NA}</dd>
          <dt className="text-muted-foreground">Unverified citation rate</dt>
          <dd className="tabular-nums">{pct(struggle.unverified_citation_rate)}</dd>
          <dt className="text-muted-foreground">Bounce rate</dt>
          <dd className="tabular-nums">{pct(adoption.bounce_rate)}</dd>
        </dl>
      </Section>
    </div>
  );
}

// The Adoption tab: how Google Patents is used and where people struggle. Headline numbers and the
// usage trend come from `/trends`, the section and task charts from `/metrics`; each has its own
// loading, empty and error state, so one failing leaves the other working. Every section or task bar
// opens the flagged conversations behind it, filtered by that section or task and by the console's
// source. Props: `filters`, `reloadKey`, `onOpenTab(tabId, anchorId?)` (the shell's tab props).
export default function AdoptionPanel({ filters, reloadKey = 0, onOpenTab }) {
  const [filter, setFilter] = useState(NO_FILTER);
  const flaggedRef = useRef(null);

  const trends = useConsoleQuery(assistTrends, filters, reloadKey, { isEmpty: isEmptyTrends });
  const metrics = useConsoleQuery(assistMetrics, filters, reloadKey, { isEmpty: isEmptyMetrics });
  const flagged = useConsoleQuery(
    ({ source }) =>
      assistFlagged({
        intent: filter.intent ?? undefined,
        section: filter.section ?? undefined,
        limit: FLAGGED_LIMIT,
        source,
      }),
    { ...FLAGGED_QUERY_FILTERS, source: filters?.source },
    `${reloadKey}:${filter.intent ?? ""}:${filter.section ?? ""}`,
    { isEmpty: never }
  );

  const trendsData = trends.state === "ready" ? trends.data : null;
  const metricsData = metrics.state === "ready" ? metrics.data : null;
  // While the list reloads for a new filter, the old list would be about something else: hide it.
  const flaggedBusy = flagged.state === "loading" || flagged.refreshing;
  const flaggedItems = !flaggedBusy && flagged.state === "ready" ? flagged.data : null;

  function pick(kind, name) {
    if (!name) return;
    setFilter(kind === "section" ? { intent: null, section: name } : { intent: name, section: null });
    flaggedRef.current?.focus();
    flaggedRef.current?.scrollIntoView?.({ block: "start" });
  }

  return (
    <div className="space-y-8">
      <RefreshFrame refreshing={trends.refreshing}>
        <PanelState state={trends.state} what="the usage trend" error={trends.error} onRetry={trends.retry}>
          {trendsData ? (
            <div className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {kpiCards(trendsData).map((kpi) => (
                  <div key={kpi.id} data-kpi={kpi.id} className="min-w-0">
                    <KpiCard {...kpi.props} />
                    {kpi.note ? (
                      <p data-kpi-note className="mt-1 text-xs text-muted-foreground">
                        {kpi.note}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
              <UsageTrend trends={trendsData} />
            </div>
          ) : null}
        </PanelState>
      </RefreshFrame>

      <RefreshFrame refreshing={metrics.refreshing}>
        <PanelState state={metrics.state} what="the adoption numbers" error={metrics.error} onRetry={metrics.retry}>
          {metricsData ? <MetricPanels adoption={metricsData.adoption ?? {}} onPick={pick} /> : null}
        </PanelState>
      </RefreshFrame>

      <section aria-labelledby="definitions-heading" className="rounded-md border border-border bg-muted/40 p-4">
        <h3 id="definitions-heading" className="text-sm font-semibold text-foreground">
          Definitions
        </h3>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
          {DEFINITIONS.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      <InsightsBrief />

      <VersionComparison comparison={metricsData?.version_comparison} />

      <FlaggedList
        ref={flaggedRef}
        items={flaggedItems}
        loading={flaggedBusy}
        error={flagged.state === "error" ? flagged.error : null}
        filter={filter}
        note={FLAGGED_NOTE}
        onClearFilter={() => setFilter(NO_FILTER)}
        onRetry={flagged.retry}
        onReview={() => onOpenTab?.("evolution")}
      />
    </div>
  );
}

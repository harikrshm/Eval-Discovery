import { useRef, useState } from "react";
import { assistEvaluateNow, assistFlagged, assistQuality, assistTrends } from "../../api";
import { useConsoleQuery } from "../../hooks/useConsoleQuery";
import { Button } from "../ui/button";
import BarsChart from "./charts/BarsChart";
import ChartCard from "./charts/ChartCard";
import KpiCard from "./charts/KpiCard";
import PanelState from "./charts/PanelState";
import TrendChart from "./charts/TrendChart";
import FlaggedList from "./FlaggedList";
import InsightsBrief from "./InsightsBrief";
import RefreshFrame from "./RefreshFrame";
import { chartMarkers } from "./chartMarkers";
import { scopeText } from "./consoleFilters";
import { isNumber, utcStamp } from "./format";

const FLAGGED_LIMIT = 50;

// The judge's reasons are a fixed set of labels. This is the only place they get readable text; a
// reason that is not in this map (or is "other") is shown as "Other" and never printed as it came.
const REASON_LABELS = {
  no_issue: "No issue found",
  missed_question: "Missed the question",
  off_topic: "Off topic",
  too_long: "Too long",
  unsupported_claim: "Unsupported claim",
  bad_citation: "Bad citation",
  legal_advice_given: "Gave legal advice",
  refusal_mishandled: "Mishandled a refusal",
  other: "Other",
};
const OTHER = REASON_LABELS.other;

const RATING_LABELS = [
  ["up", "Thumbs-up"],
  ["down", "Thumbs-down"],
  ["no_answer", "No answer"],
  ["none", "No rating"],
];

// The flagged-list filter chips and the `reason` each one asks the backend for.
const REASON_CHIPS = [
  { label: "All", reason: null },
  { label: "Thumbs-down", reason: "rated_down" },
  { label: "No answer", reason: "no_answer" },
  { label: "Re-ask", reason: "reasked" },
  { label: "Citation failure", reason: "citation_unverified" },
  { label: "Auto-judge", reason: "auto_judge" },
];

const GRADE_SERIES = [{ key: "avg_grade", label: "Average grade (1 to 5)" }];
const COUNT_SERIES = [{ key: "value", label: "Conversations" }];

const JUDGE_DISCLAIMER =
  "The grade and verdicts are the opinion of a language model that reads each conversation. They have not been validated against human raters, so treat them as a signal to look into, not as a measurement.";
const DEMO_NOTE =
  "This view includes demo dataset conversations. Their judgements are generated for the demo, not measured.";
const GENERIC_ERROR = "Something went wrong.";

const never = () => false;

function count(value) {
  return isNumber(value) ? value : 0;
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

// Nothing to show when nothing was judged, nothing waits to be judged and nothing was rated.
function isEmptyQuality(data) {
  if (!data) return true;
  const rated = RATING_LABELS.some(([key]) => count(data.ratings?.[key]) > 0);
  return count(data.judged) === 0 && count(data.unjudged) === 0 && !rated;
}

// The average grade over the whole range: each bucket's average weighted by how many conversations it
// judged. Unknown (null) when no bucket has a grade, never 0.
function averageGrade(trend) {
  let sum = 0;
  let n = 0;
  for (const point of trend) {
    if (isNumber(point.avg_grade) && count(point.n) > 0) {
      sum += point.avg_grade * point.n;
      n += point.n;
    }
  }
  return n > 0 ? round2(sum / n) : null;
}

function share(value) {
  return isNumber(value) ? round1(value * 100) : null;
}

function unjudgedText(unjudged) {
  const n = count(unjudged);
  if (n === 0) return "No conversations are waiting to be judged.";
  return n === 1 ? "1 conversation is still unjudged." : `${n} conversations are still unjudged.`;
}

// The reasons chart: every fixed reason in order, and everything else (including the backend's own
// "other") added into one "Other" row.
function reasonRows(reasons) {
  const totals = new Map(Object.values(REASON_LABELS).map((label) => [label, 0]));
  for (const [key, value] of Object.entries(reasons ?? {})) {
    const label = Object.hasOwn(REASON_LABELS, key) ? REASON_LABELS[key] : OTHER;
    totals.set(label, totals.get(label) + count(value));
  }
  return [...totals].map(([name, value]) => ({ name, value }));
}

function CountsCard({ title, subtitle, firstColumn, rows }) {
  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      tableColumns={[firstColumn, "Conversations"]}
      tableRows={rows.map((row) => [row.name, row.value])}
    >
      <BarsChart data={rows} categoryKey="name" series={COUNT_SERIES} />
    </ChartCard>
  );
}

function NoDataCard({ title, text = "Not enough data yet." }) {
  return (
    <section aria-label={title} className="space-y-2 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="text-sm text-muted-foreground">{text}</p>
    </section>
  );
}

function GradeTrend({ quality, markers }) {
  const trend = quality.grade_trend ?? [];
  if (!trend.some((p) => isNumber(p.avg_grade))) {
    return <NoDataCard title="Average judge grade over time" text="No graded conversations in this range yet." />;
  }
  return (
    <ChartCard
      title="Average judge grade over time"
      subtitle="Grades run from 1 (worst) to 5 (best); a gap is a period with no judged conversation. Dashed lines mark when a prompt version went live. Times are UTC."
      tableColumns={["Time (UTC)", "Average grade", "Judged"]}
      tableRows={trend.map((p) => [utcStamp(p.t), isNumber(p.avg_grade) ? p.avg_grade : null, count(p.n)])}
    >
      <TrendChart
        data={trend}
        xKey="t"
        series={GRADE_SERIES}
        bucket={quality.bucket === "hour" ? "hour" : "day"}
        markers={chartMarkers(markers)}
      />
    </ChartCard>
  );
}

function QualityData({ quality, markers, showDemoNote }) {
  const answered = quality.answered ?? {};
  const legal = quality.legal ?? {};
  const judged = count(quality.judged) > 0;
  const kpis = [
    { id: "judged", props: { label: "Judged conversations", value: count(quality.judged) } },
    { id: "avg_grade", props: { label: "Average grade (1 to 5)", value: averageGrade(quality.grade_trend ?? []) } },
    { id: "concise_share", props: { label: "Concise answers", value: share(quality.concise_share), unit: "%" } },
    { id: "citations_share", props: { label: "Citations make sense", value: share(quality.citations_share), unit: "%" } },
  ];
  const verdictRows = [
    { name: "Answered: yes", value: count(answered.yes) },
    { name: "Answered: partly", value: count(answered.partly) },
    { name: "Answered: no", value: count(answered.no) },
    { name: "Legal question: handled", value: count(legal.handled) },
    { name: "Legal question: mishandled", value: count(legal.mishandled) },
  ];
  const ratingRows = RATING_LABELS.map(([key, name]) => ({ name, value: count(quality.ratings?.[key]) }));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {kpis.map((k) => (
          <div key={k.id} data-kpi={k.id} className="min-w-0">
            <KpiCard {...k.props} />
          </div>
        ))}
      </div>
      <p data-unjudged className="text-sm text-muted-foreground">
        {unjudgedText(quality.unjudged)}
      </p>
      {showDemoNote ? (
        <p data-demo-note className="text-xs font-medium text-amber-900">
          {DEMO_NOTE}
        </p>
      ) : null}

      <GradeTrend quality={quality} markers={markers} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="min-w-0">
          {judged ? (
            <CountsCard
              title="Verdicts"
              subtitle="Did the answer address the question, and was a legal question handled safely."
              firstColumn="Verdict"
              rows={verdictRows}
            />
          ) : (
            <NoDataCard title="Verdicts" />
          )}
        </div>
        <div className="min-w-0">
          {judged ? (
            <CountsCard
              title="Reasons"
              subtitle="The main reason the judge gave for each grade."
              firstColumn="Reason"
              rows={reasonRows(quality.reasons)}
            />
          ) : (
            <NoDataCard title="Reasons" />
          )}
        </div>
        <div className="min-w-0 lg:col-span-2">
          <CountsCard
            title="Ratings"
            subtitle="What people gave when asked, for comparison with the judge. Not every conversation is rated."
            firstColumn="Rating"
            rows={ratingRows}
          />
        </div>
      </div>
    </div>
  );
}

function EvaluateNow() {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const busy = useRef(false);

  async function run() {
    if (busy.current) return;
    busy.current = true;
    setPending(true);
    setError(null);
    setResult(null);
    try {
      setResult(await assistEvaluateNow());
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : GENERIC_ERROR);
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  const queued = count(result?.queued);
  const waiting = count(result?.waiting);
  let status = "";
  if (pending) status = "Evaluating…";
  else if (result) status = queued === 0 && waiting === 0 ? "Nothing to evaluate" : `Queued ${queued}; ${waiting} waiting for the next hour`;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" size="sm" onClick={run} disabled={pending}>
          Evaluate now
        </Button>
        <p data-evaluate-status role="status" className="text-sm text-muted-foreground">
          {status}
        </p>
      </div>
      {error ? (
        <p data-evaluate-error role="alert" className="text-sm text-red-700">
          Couldn&apos;t start the evaluation: {error}
        </p>
      ) : null}
    </div>
  );
}

function ReasonChips({ value, onChange }) {
  return (
    <div role="group" aria-label="Flagged conversations by reason" className="flex flex-wrap gap-2">
      {REASON_CHIPS.map((chip) => {
        const pressed = value === chip.reason;
        return (
          <button
            key={chip.label}
            type="button"
            aria-pressed={pressed}
            onClick={() => onChange(chip.reason)}
            className={`h-8 whitespace-nowrap rounded-full border px-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] ${
              pressed
                ? "border-foreground bg-foreground text-background"
                : "border-border bg-background text-foreground hover:bg-muted"
            }`}
          >
            {chip.label}
          </button>
        );
      })}
    </div>
  );
}

// The Quality tab: how well the assistant answered, as graded by the auto-judge. It shows the judged
// numbers, the average grade over time with the prompt versions marked, the verdicts and reasons, the
// ratings people gave, the flagged conversations (filterable by reason), the insights brief, and an
// "Evaluate now" button that asks the judge to look at waiting conversations. Only fixed labels and
// counts are shown, never question or answer text. Props: `filters`, `reloadKey`, `onOpenTab(tabId,
// anchorId?)` (the shell's tab props). The section with the judge numbers has the DOM id
// `quality-judge`, which an alert scrolls to; it is always present, whatever the state of the data.
export default function QualityTab({ filters, reloadKey = 0, onOpenTab }) {
  const [reason, setReason] = useState(null);

  const quality = useConsoleQuery(assistQuality, filters, reloadKey, { isEmpty: isEmptyQuality });
  // The quality payload has no version markers; they come from the trends payload. They are only
  // decoration, so a failed or pending call just means no markers.
  const trends = useConsoleQuery(assistTrends, filters, reloadKey, { isEmpty: never });
  const flagged = useConsoleQuery(
    ({ range, source, version }) =>
      assistFlagged({ reason: reason ?? undefined, limit: FLAGGED_LIMIT, range, source, version }),
    filters,
    `${reloadKey}:${reason ?? ""}`,
    { isEmpty: never }
  );

  const qualityData = quality.state === "ready" ? quality.data : null;
  const markers = trends.state === "ready" && !trends.refreshing ? trends.data?.version_markers : [];
  // While the list reloads for a new reason, the old list would be about something else: hide it.
  const flaggedBusy = flagged.state === "loading" || flagged.refreshing;
  const flaggedItems = !flaggedBusy && flagged.state === "ready" ? flagged.data : null;

  return (
    <div className="space-y-8">
      <section id="quality-judge" aria-labelledby="quality-judge-title" className="space-y-4">
        <h3 id="quality-judge-title" className="text-sm font-semibold text-foreground">
          Auto-judge results
        </h3>
        <p data-judge-disclaimer className="text-sm text-muted-foreground">
          {JUDGE_DISCLAIMER}
        </p>
        <EvaluateNow />
        <RefreshFrame refreshing={quality.refreshing}>
          <PanelState state={quality.state} what="the quality numbers" error={quality.error} onRetry={quality.retry}>
            {qualityData ? (
              <QualityData quality={qualityData} markers={markers} showDemoNote={filters?.source !== "live"} />
            ) : null}
          </PanelState>
        </RefreshFrame>
      </section>

      <FlaggedList
        items={flaggedItems}
        loading={flaggedBusy}
        error={flagged.state === "error" ? flagged.error : null}
        filter={{}}
        scope={scopeText(filters)}
        controls={<ReasonChips value={reason} onChange={setReason} />}
        onClearFilter={() => setReason(null)}
        onRetry={flagged.retry}
        onReview={() => onOpenTab?.("evolution")}
      />

      <InsightsBrief />
    </div>
  );
}

import { assistOperations } from "../../api";
import { useConsoleQuery } from "../../hooks/useConsoleQuery";
import BarsChart from "./charts/BarsChart";
import ChartCard from "./charts/ChartCard";
import KpiCard from "./charts/KpiCard";
import PanelState from "./charts/PanelState";
import TrendChart from "./charts/TrendChart";
import RefreshFrame from "./RefreshFrame";
import { NA, isNumber, pct, utcStamp } from "./format";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const LATENCY_SERIES = [
  { key: "p50_ms", label: "Median (p50)" },
  { key: "p95_ms", label: "Slowest 5% (p95)" },
];
const OUTCOME_SERIES = [
  { key: "ok", label: "OK" },
  { key: "error", label: "Error" },
  { key: "rate_limited", label: "Rate limited" },
  { key: "timeout", label: "Timeout" },
];
const CALL_SERIES = [{ key: "n", label: "Calls" }];
const TOKEN_SERIES = [{ key: "tokens", label: "Tokens" }];
const TOKEN_KINDS = [
  ["chat", "Chat"],
  ["judge", "Judge"],
  ["insights", "Insights"],
];

// The background job types the backend can list. Anything else is shown as it came, as plain text.
const JOB_LABELS = {
  build_tests: "Build tests",
  replay: "Replay",
  suggest_fix: "Suggest a fix",
  generate_heldout: "Generate held-out tests",
  change_report: "Change report",
  judge_conversation: "Judge a conversation",
};

const SEMANTICS = [
  "Latency is the total time a user waited, including retries.",
  "Percentiles use chat calls only; counts and tokens include all call kinds.",
];
const DEMO_NOTE =
  "This view includes demo dataset rows. They are imported for the demo, not real usage.";
const NO_CALLS = "No calls in this range yet.";
const NO_MESSAGE = "No message recorded.";

function count(value) {
  return isNumber(value) ? value : 0;
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

// 850 -> "850 ms", 1900 -> "1.9 s". Above one second it reads as seconds with one decimal.
function formatMs(ms) {
  if (!isNumber(ms)) return null;
  return ms > 1000 ? `${round1(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function formatCount(value) {
  return isNumber(value) ? value.toLocaleString("en-US") : null;
}

// 42 -> "42 min", 135 -> "2 h 15 min"; nothing waiting is unknown, not zero.
function formatWait(minutes) {
  if (!isNumber(minutes)) return NA;
  const total = Math.round(minutes);
  if (total < 60) return `${total} min`;
  return `${Math.floor(total / 60)} h ${total % 60} min`;
}

// A bucket as the chart's category: the clock time for hourly buckets (counted back from the request
// time, so not on the hour), the date for daily ones. Always UTC; the tables carry the full stamp.
function bucketLabel(t, bucket) {
  const stamp = utcStamp(t);
  if (stamp === NA) return null;
  const [date, time] = stamp.split(" ");
  if (bucket === "hour") return time;
  const [, month, day] = date.split("-");
  return `${MONTHS[Number(month) - 1]} ${Number(day)}`;
}

function sum(rows, key) {
  return rows.reduce((total, row) => total + count(row?.[key]), 0);
}

function outcomeCalls(row) {
  return OUTCOME_SERIES.reduce((total, s) => total + count(row?.[s.key]), 0);
}

function isIdle(queue) {
  return !queue || [queue.waiting_sessions, queue.queued_jobs, queue.judged_last_hour, queue.unreadable_sessions].every((n) => count(n) === 0);
}

// Nothing to show when no call was recorded, nothing waits for the judge and no job failed.
function isEmptyOperations(data) {
  if (!data) return true;
  return count(data.totals?.calls) === 0 && isIdle(data.judge_queue) && !(data.recent_failed_jobs ?? []).length;
}

function KpiRow({ ops }) {
  const totals = ops.totals ?? {};
  const rateLimited = isNumber(totals.rate_limit_rate) ? sum(ops.outcomes ?? [], "rate_limited") : null;
  const errorRate = isNumber(totals.error_rate) ? round1(totals.error_rate * 100) : null;
  const kpis = [
    { id: "p95", props: { label: "Latency p95", value: formatMs(totals.p95_ms) } },
    { id: "error_rate", props: { label: "Error rate", value: errorRate, unit: "%" } },
    {
      id: "rate_limited",
      props: {
        label: "Rate-limited calls",
        value: rateLimited,
        unit: rateLimited === null ? null : `${pct(totals.rate_limit_rate)} of calls`,
      },
    },
    { id: "tokens", props: { label: "Tokens in range", value: formatCount(totals.tokens) } },
  ];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {kpis.map((k) => (
        <div key={k.id} data-kpi={k.id} className="min-w-0">
          <KpiCard {...k.props} />
        </div>
      ))}
    </div>
  );
}

// The anchored place of one chart. The element with the DOM id an alert scrolls to is this wrapper, and
// it is the same element in every state (loading, error, nothing to draw, chart), so a scroll to it is
// never aimed at something that is replaced a moment later. Without a chart it still shows its heading
// and a plain line about what belongs here.
function Slot({ id, title, description, text = null, card = null }) {
  return (
    <div id={id} className="min-w-0">
      {card ?? (
        <section aria-label={title} className="space-y-2 rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          <p className="text-xs text-muted-foreground">{description}</p>
          {text ? <p className="text-sm text-muted-foreground">{text}</p> : null}
        </section>
      )}
    </div>
  );
}

function NoDataCard({ title, text = NO_CALLS }) {
  return (
    <section aria-label={title} className="space-y-2 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="text-sm text-muted-foreground">{text}</p>
    </section>
  );
}

const LATENCY_TITLE = "Latency over time";
const LATENCY_HELP = "The median and the slowest 5% of the time a user waited for a chat answer.";
const OUTCOMES_TITLE = "Calls by outcome";
const OUTCOMES_HELP = "How each call to the language model ended: fine, failed, turned away for rate limits, or timed out.";

function LatencySlot({ ops }) {
  const bucket = ops?.bucket === "hour" ? "hour" : "day";
  const rows = (ops?.latency ?? []).filter((p) => utcStamp(p.t) !== NA);
  const known = rows.some((p) => isNumber(p.p50_ms) || isNumber(p.p95_ms));
  let card = null;
  let text = null;
  if (ops && known) {
    card = (
      <ChartCard
        title={LATENCY_TITLE}
        subtitle={`${LATENCY_HELP} A gap is a period with no timed chat call. Times are UTC.`}
        tableColumns={["Time (UTC)", "Median (p50)", "Slowest 5% (p95)", "Chat calls"]}
        tableRows={rows.map((p) => [utcStamp(p.t), formatMs(p.p50_ms), formatMs(p.p95_ms), count(p.n)])}
      >
        <TrendChart data={rows} xKey="t" series={LATENCY_SERIES} bucket={bucket} yFormat={(v) => formatMs(v) ?? NA} />
      </ChartCard>
    );
  } else if (ops) {
    text = "No timed chat calls in this range yet.";
  }
  return <Slot id="ops-latency" title={LATENCY_TITLE} description={LATENCY_HELP} text={text} card={card} />;
}

function OutcomesSlot({ ops }) {
  const bucket = ops?.bucket === "hour" ? "hour" : "day";
  const rows = (ops?.outcomes ?? []).filter((p) => utcStamp(p.t) !== NA);
  const totals = OUTCOME_SERIES.map((s) => ({ ...s, total: sum(rows, s.key) }));
  let card = null;
  let text = null;
  if (ops && rows.some((p) => outcomeCalls(p) > 0)) {
    const data = rows.map((p) => ({ name: bucketLabel(p.t, bucket), ...Object.fromEntries(OUTCOME_SERIES.map((s) => [s.key, count(p[s.key])])) }));
    card = (
      <ChartCard
        title={OUTCOMES_TITLE}
        subtitle={`${OUTCOMES_HELP} Times are UTC.`}
        tableColumns={["Time (UTC)", ...OUTCOME_SERIES.map((s) => s.label)]}
        tableRows={rows.map((p) => [utcStamp(p.t), ...OUTCOME_SERIES.map((s) => count(p[s.key]))])}
      >
        <BarsChart data={data} categoryKey="name" series={OUTCOME_SERIES} layout="vertical" grouped={false} />
        <p data-outcome-totals className="mt-2 text-xs text-muted-foreground">
          In this range: {totals.map((s) => `${s.label} ${s.total}`).join(", ")}.
        </p>
      </ChartCard>
    );
  } else if (ops) {
    text = NO_CALLS;
  }
  return <Slot id="ops-errors" title={OUTCOMES_TITLE} description={OUTCOMES_HELP} text={text} card={card} />;
}

function RequestsCard({ ops }) {
  const bucket = ops.bucket === "hour" ? "hour" : "day";
  const rows = (ops.requests ?? []).filter((p) => utcStamp(p.t) !== NA);
  const title = "Calls over time";
  if (!rows.some((p) => count(p.n) > 0)) return <NoDataCard title={title} />;
  const data = rows.map((p) => ({ name: bucketLabel(p.t, bucket), n: count(p.n) }));
  return (
    <ChartCard
      title={title}
      subtitle="Every call to the language model: chat, judge and insights. Times are UTC."
      tableColumns={["Time (UTC)", "Calls"]}
      tableRows={rows.map((p) => [utcStamp(p.t), count(p.n)])}
    >
      <BarsChart data={data} categoryKey="name" series={CALL_SERIES} layout="vertical" valueFormat={formatCount} />
    </ChartCard>
  );
}

function TokensCard({ ops }) {
  const title = "Tokens by kind of call";
  const rows = TOKEN_KINDS.map(([key, name]) => ({ name, tokens: count(ops.tokens_by_kind?.[key]) }));
  if (count(ops.totals?.calls) === 0 && sum(rows, "tokens") === 0) return <NoDataCard title={title} />;
  return (
    <ChartCard
      title={title}
      subtitle="Chat is the answers users see; judge and insights are the background jobs."
      tableColumns={["Kind of call", "Tokens"]}
      tableRows={rows.map((r) => [r.name, formatCount(r.tokens)])}
    >
      <BarsChart data={rows} categoryKey="name" series={TOKEN_SERIES} layout="horizontal" valueFormat={formatCount} />
    </ChartCard>
  );
}

function Stat({ id, label, value }) {
  return (
    <div data-queue-stat={id} className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-lg font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function JudgeQueue({ queue }) {
  const cap = isNumber(queue?.hourly_cap) ? queue.hourly_cap : null;
  return (
    <section data-queue aria-label="Judge queue" className="space-y-3 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-foreground">Judge queue</h3>
      {queue ? (
        <>
          <p data-queue-summary className="text-2xl font-semibold text-foreground">
            {count(queue.waiting_sessions)} waiting
          </p>
          <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat id="queued_jobs" label="Queued judge jobs" value={count(queue.queued_jobs)} />
            <Stat id="oldest" label="Oldest waiting" value={formatWait(queue.oldest_waiting_minutes)} />
            <Stat
              id="judged"
              label="Judged in the last hour"
              value={cap === null ? count(queue.judged_last_hour) : `${count(queue.judged_last_hour)} of ${cap}`}
            />
            <Stat id="unreadable" label="Unreadable conversations" value={count(queue.unreadable_sessions)} />
          </dl>
        </>
      ) : null}
      <p data-queue-explanation className="text-xs text-muted-foreground">
        The judge reads one conversation at a time and at most {cap ?? "a fixed number of"} conversations an hour (the
        hourly cap), so a backlog clears slowly on purpose and never crowds out chat. An unreadable conversation is one
        the judge could not read; it is not tried again until a new question arrives in it.
      </p>
    </section>
  );
}

function FailedJobs({ jobs }) {
  return (
    <section data-failed-jobs aria-label="Recent failed jobs" className="space-y-3 rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold text-foreground">Recent failed jobs</h3>
      {jobs.length === 0 ? (
        <p className="text-sm text-muted-foreground">No failed jobs.</p>
      ) : (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-border">
                {["Type", "Time (UTC)", "Message"].map((name) => (
                  <th key={name} scope="col" className="py-1 pr-3 font-medium text-foreground">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {jobs.map((job, i) => (
                <tr key={job.id ?? i} className="border-b border-border/60 align-top last:border-0">
                  <td className="py-1 pr-3 text-foreground">
                    {Object.hasOwn(JOB_LABELS, job.type) ? JOB_LABELS[job.type] : String(job.type ?? NA)}
                  </td>
                  <td className="whitespace-nowrap py-1 pr-3 tabular-nums text-muted-foreground">{utcStamp(job.created_at)}</td>
                  <td className="break-words py-1 pr-3 text-muted-foreground">
                    {typeof job.error === "string" && job.error ? job.error : NO_MESSAGE}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// The Operations tab: how the assistant runs. The headline numbers (latency, errors, rate limits,
// tokens), latency over time, how calls ended, how many were made, tokens by kind of call, the queue
// of conversations waiting for the auto-judge and the newest failed background jobs. Counts, rates and
// fixed labels only; whatever text arrives is rendered as plain text. Props: `filters`, `reloadKey`
// (the shell's tab props). The wrappers around the latency card and the outcomes card have the DOM ids
// `ops-latency` and `ops-errors`, which alerts scroll to; both are present in every state, as are the
// plain explanations of what the numbers mean.
export default function OperationsTab({ filters, reloadKey = 0 }) {
  const query = useConsoleQuery(assistOperations, filters, reloadKey, { isEmpty: isEmptyOperations });
  const ops = query.state === "ready" ? query.data : null;

  return (
    <div className="space-y-4">
      <p data-semantics className="text-sm text-muted-foreground">
        {SEMANTICS.join(" ")}
      </p>
      <RefreshFrame refreshing={query.refreshing}>
        <div className="space-y-4">
          <PanelState state={query.state} what="the operations data" error={query.error} onRetry={query.retry}>
            {ops ? (
              <>
                <KpiRow ops={ops} />
                {filters?.source !== "live" ? (
                  <p data-demo-note className="text-xs font-medium text-amber-900">
                    {DEMO_NOTE}
                  </p>
                ) : null}
              </>
            ) : null}
          </PanelState>

          <LatencySlot ops={ops} />
          <OutcomesSlot ops={ops} />

          {ops ? (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="min-w-0">
                <RequestsCard ops={ops} />
              </div>
              <div className="min-w-0">
                <TokensCard ops={ops} />
              </div>
            </div>
          ) : null}

          <JudgeQueue queue={ops?.judge_queue ?? null} />
          {ops ? <FailedJobs jobs={ops.recent_failed_jobs ?? []} /> : null}
        </div>
      </RefreshFrame>
    </div>
  );
}

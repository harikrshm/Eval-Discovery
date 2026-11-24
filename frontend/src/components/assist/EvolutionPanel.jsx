import { useState } from "react";
import { ApiError, assistPipeline, checkPattern, getReport, getVersions } from "../../api";
import { useConsoleQuery } from "../../hooks/useConsoleQuery";
import { useEvalLoop } from "../../hooks/useEvalLoop";
import { readiness } from "../../lib/readiness";
import EvalCriteriaPanel from "../EvalCriteriaPanel";
import ReadinessChip from "../ReadinessChip";
import { Button } from "../ui/button";
import LoopStepper from "./LoopStepper";
import RefreshFrame from "./RefreshFrame";
import ReplayChart from "./ReplayChart";
import VersionTimeline from "./VersionTimeline";
import PanelState from "./charts/PanelState";
import { replayCandidates, replayRow } from "./replayData";

const CHECK_FAILED = "The pattern check failed. Please try again.";

// The loop belongs to the agent, not to a time range: these queries take no console filters.
const NO_FILTERS = {};

// One stored outcome per verified version, read one by one (plain reads, not report views).
// `null` while the version list is not known yet.
async function loadReplays(versions) {
  if (versions === null) return null;
  const candidates = replayCandidates(versions);
  const reports = await Promise.all(candidates.map((version) => getReport(version.id)));
  return candidates.map((version, i) => replayRow(version, reports[i])).filter(Boolean);
}

// What the existing buttons and panels show about how far the loop has come. When it changes, the
// pipeline and the version list are read again so the stepper never trails the buttons. `null`
// until the agent and its suite have loaded (the first load is not a change).
function loopSignature({ ready, found, patternSummary, criteria, suite }) {
  if (!ready || !suite) return null;
  const tests = (suite.criteria ?? []).reduce((n, c) => n + (c.tests?.length ?? 0), 0);
  const candidate = suite.candidate ? `${suite.candidate.version_id}:${suite.candidate.status}` : "-";
  return [found || patternSummary ? 1 : 0, criteria, tests, candidate, suite.prompt_version_id ?? "-"].join("|");
}

// The Evolution tab: the loop as a pipeline and a version timeline with a before/after replay chart
// on top, then the existing improvement loop (criteria, tests, change review, version history,
// prompt editor, readiness) for the assistant agent, wired through the same hook as the chat app.
// Two explicit buttons start it from the flagged conversations. The tab ignores the console
// filters (the loop belongs to the whole agent) but refetches on Refresh (`reloadKey`).
export default function EvolutionPanel({ agentId, reloadKey = 0 }) {
  const loop = useEvalLoop(agentId);
  const { session, tests, criteriaError, panelProps } = loop;
  const { loading, loadError, retry, evalCriteria, patternState, patternSummary, setPatternState } = session;

  const [check, setCheck] = useState({ status: "idle", found: false, summary: null, error: null });

  const signature = loopSignature({
    ready: !loading && !loadError,
    found: check.found,
    patternSummary,
    criteria: evalCriteria.length,
    suite: tests.suite,
  });
  // Adjusts state during render, keyed on the previous signature (as useEvalLoop does).
  const [seenSignature, setSeenSignature] = useState(null);
  const [loopMoves, setLoopMoves] = useState(0);
  if (signature !== null && signature !== seenSignature) {
    setSeenSignature(signature);
    if (seenSignature !== null) setLoopMoves((n) => n + 1);
  }

  // Only an explicit Refresh (`reloadKey`) may dim the tab: the base key is the Refresh, and what the
  // loop itself moves (and the verified versions the chart reads) re-key quietly.
  const pipeline = useConsoleQuery(assistPipeline, NO_FILTERS, reloadKey, {
    isEmpty: (data) => !data?.steps?.length,
    quietKey: loopMoves,
  });
  const versions = useConsoleQuery(() => getVersions(agentId), NO_FILTERS, `${reloadKey}:${agentId}`, {
    quietKey: loopMoves,
  });
  const candidateIds =
    versions.data === null ? "pending" : replayCandidates(versions.data).map((v) => `${v.id}:${v.status}`).join(",");
  const replays = useConsoleQuery(() => loadReplays(versions.data), NO_FILTERS, reloadKey, { quietKey: candidateIds });
  const refreshing = pipeline.refreshing || versions.refreshing || replays.refreshing;

  // The chart needs the version list first: its failure or wait is the chart's too.
  const chart =
    versions.state === "loading" || versions.state === "error"
      ? { state: versions.state, error: versions.error, retry: versions.retry }
      : { state: replays.state, error: replays.error, retry: replays.retry };

  async function runCheck() {
    if (check.status === "checking") return;
    setCheck({ status: "checking", found: false, summary: null, error: null });
    try {
      const result = await checkPattern(agentId);
      const found = Boolean(result?.found);
      setCheck({ status: "done", found, summary: result?.pattern_summary ?? null, error: null });
      setPatternState(found ? "pattern_found" : "pattern_not_found");
    } catch (err) {
      const error = err instanceof ApiError && err.message ? err.message : CHECK_FAILED;
      setCheck({ status: "error", found: false, summary: null, error });
    }
  }

  const generating = patternState === "generating";
  const showDiscover = check.found || Boolean(patternSummary);

  async function runDiscover() {
    const ok = await loop.discoverEvals({ requireSummary: false });
    if (ok) setCheck({ status: "idle", found: false, summary: null, error: null });
  }

  let actions;
  if (loadError) {
    actions = (
      <div role="alert" className="space-y-2 text-sm text-red-700">
        <p>Couldn&apos;t load the assistant agent: {loadError}</p>
        <Button type="button" variant="outline" size="sm" onClick={retry}>
          Retry
        </Button>
      </div>
    );
  } else if (loading) {
    actions = (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the assistant agent…
      </p>
    );
  } else {
    actions = (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" size="sm" onClick={runCheck} disabled={check.status === "checking" || generating}>
            Check flagged conversations for a pattern
          </Button>
          {showDiscover && (
            <Button type="button" variant="accent" size="sm" onClick={runDiscover} disabled={generating}>
              Discover evals
            </Button>
          )}
          <ReadinessChip readiness={evalCriteria.length > 0 && tests.suite ? readiness(tests.suite) : null} />
        </div>

        <div role="status" aria-live="polite" className="space-y-1 text-sm">
          {check.status === "checking" && <p className="text-muted-foreground">Checking flagged conversations…</p>}
          {generating && <p className="text-muted-foreground">Generating eval…</p>}
          {check.status === "done" && check.found && <p className="text-green-800">Pattern found: {check.summary}</p>}
          {check.status === "done" && !check.found && (
            <p className="text-muted-foreground">No pattern found in the flagged conversations.</p>
          )}
          {check.status !== "done" && check.status !== "checking" && patternSummary && (
            <p className="text-green-800">Pattern found: {patternSummary}</p>
          )}
        </div>
        {check.error && (
          <p role="alert" className="text-sm text-red-700">
            {check.error}
          </p>
        )}
        {criteriaError && (
          <p role="alert" className="text-sm text-red-700">
            {criteriaError}
          </p>
        )}

        {evalCriteria.length > 0 ? (
          <div className="h-[75vh] min-h-[28rem] overflow-hidden rounded-md border border-border">
            <EvalCriteriaPanel key={agentId} {...panelProps} discoverPending={showDiscover} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No eval criteria yet. Check the flagged conversations for a pattern, then discover evals.
          </p>
        )}
      </div>
    );
  }

  return (
    <RefreshFrame refreshing={refreshing}>
      <div className="space-y-6">
        <p className="text-xs text-muted-foreground">Applies to the whole assistant, not the selected range.</p>

        <section id="evolution-loop" aria-labelledby="evolution-loop-title" className="space-y-3">
          <h3 id="evolution-loop-title" className="text-sm font-semibold text-foreground">
            Improvement loop
          </h3>
          <PanelState state={pipeline.state} what="the improvement loop" error={pipeline.error} onRetry={pipeline.retry}>
            {pipeline.state === "ready" && pipeline.data ? (
              <>
                <LoopStepper steps={pipeline.data.steps} />
                {pipeline.data.cycle_note ? <p className="text-xs text-muted-foreground">{pipeline.data.cycle_note}</p> : null}
              </>
            ) : null}
          </PanelState>
        </section>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <section aria-labelledby="evolution-timeline-title" className="min-w-0 space-y-3">
            <h3 id="evolution-timeline-title" className="text-sm font-semibold text-foreground">
              Version timeline
            </h3>
            {versions.state === "empty" ? (
              <p className="text-sm text-muted-foreground">No versions yet.</p>
            ) : (
              <PanelState state={versions.state} what="the version timeline" error={versions.error} onRetry={versions.retry}>
                {versions.state === "ready" ? <VersionTimeline versions={versions.data} /> : null}
              </PanelState>
            )}
          </section>

          <div className="min-w-0">
            {chart.state === "ready" && replays.data ? (
              <ReplayChart rows={replays.data} />
            ) : (
              <section aria-labelledby="evolution-replay-title" className="space-y-3">
                <h3 id="evolution-replay-title" className="text-sm font-semibold text-foreground">
                  Before and after replay
                </h3>
                {chart.state === "empty" ? (
                  <p className="text-sm text-muted-foreground">No replay results yet.</p>
                ) : (
                  <PanelState state={chart.state} what="the replay results" error={chart.error} onRetry={chart.retry} />
                )}
              </section>
            )}
          </div>
        </div>

        {actions}
      </div>
    </RefreshFrame>
  );
}

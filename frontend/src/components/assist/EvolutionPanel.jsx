import { useState } from "react";
import { ApiError, checkPattern } from "../../api";
import { useEvalLoop } from "../../hooks/useEvalLoop";
import { readiness } from "../../lib/readiness";
import EvalCriteriaPanel from "../EvalCriteriaPanel";
import ReadinessChip from "../ReadinessChip";
import { Button } from "../ui/button";

const CHECK_FAILED = "The pattern check failed. Please try again.";

// The Evolution tab: the existing improvement loop (criteria, tests, change review, version
// history, prompt editor, readiness) for the assistant agent, wired through the same hook as the
// chat app. Two explicit buttons on top start it from the flagged conversations.
export default function EvolutionPanel({ agentId }) {
  const loop = useEvalLoop(agentId);
  const { session, tests, criteriaError, panelProps } = loop;
  const { loading, loadError, retry, evalCriteria, patternState, patternSummary, setPatternState } = session;

  const [check, setCheck] = useState({ status: "idle", found: false, summary: null, error: null });

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

  if (loadError) {
    return (
      <div role="alert" className="space-y-2 text-sm text-red-700">
        <p>Couldn&apos;t load the assistant agent: {loadError}</p>
        <Button type="button" variant="outline" size="sm" onClick={retry}>
          Retry
        </Button>
      </div>
    );
  }
  if (loading) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the assistant agent…
      </p>
    );
  }

  return (
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

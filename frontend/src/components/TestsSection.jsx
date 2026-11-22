import { useState, useEffect, useId } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { SLOW_REPLY_MS } from "../lib/timing";
import { verdictLabel, statusLabel, lowConfidenceNote } from "../lib/testStatus";
import { cn } from "../lib/utils";

const ROLE_LABELS = { fix: "Fix test", heldout: "Held-out test" };
const NO_HELDOUT_TEXT = "Couldn't write a test your current prompt fails, so it may already handle this case.";
const HELDOUT_DISCARDED_TEXT = "The prompt or criterion changed while the test was being written. Try again.";

function testLabel(test) {
  if (test.origin === "generated") return "Held-out test (generated)";
  return ROLE_LABELS[test.role] ?? "Test";
}

const STATUS_STYLES = {
  passing: "bg-green-50 text-green-800 border-green-200",
  failing: "bg-red-50 text-red-700 border-red-200",
  untested: "bg-muted text-muted-foreground border-border",
};

function isActive(job) {
  return job?.status === "queued" || job?.status === "running";
}

function TestRow({ test, job, suggestJob, agentName, userRoleLabel, onReplay, onSuggestFix }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();

  const replaying = isActive(job) || (!job && Boolean(test.active_job_id));
  const failed = job?.status === "failed";
  const label = testLabel(test);
  const { baseline, current } = test;
  // The server decides whether a fix can be suggested; a held-out test never offers one.
  const canSuggest = Boolean(test.can_suggest_fix) && test.role !== "heldout";
  const suggesting = isActive(suggestJob) || (!suggestJob && Boolean(test.suggest_job_id));
  const suggestFailed = suggestJob?.status === "failed";

  return (
    <li className="rounded-md border border-border bg-white p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">{label}</p>
          <p className="text-xs text-muted-foreground">
            Original run: {verdictLabel(baseline?.verdict ?? "fail")}
          </p>
          <p aria-live="polite" className="text-xs text-muted-foreground">
            Latest replay: {verdictLabel(current?.verdict)}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={replaying}
            aria-busy={replaying}
            onClick={() => onReplay(test.id)}
          >
            {replaying ? "Replaying…" : failed ? "Retry" : "Replay"}
            <span className="sr-only"> {label}</span>
          </Button>
          {canSuggest && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={suggesting}
              aria-busy={suggesting}
              onClick={() => onSuggestFix(test.id)}
            >
              {suggesting ? "Suggesting…" : suggestFailed ? "Retry" : "Suggest fix"}
              <span className="sr-only">
                {suggestFailed && !suggesting ? ` suggest a fix for ${label}` : ` for ${label}`}
              </span>
            </Button>
          )}
        </div>
      </div>

      {failed && (
        <p role="alert" className="text-xs text-red-700">
          {job.error || "The replay failed."}
        </p>
      )}

      {canSuggest && suggestFailed && !suggesting && (
        <p role="alert" className="text-xs text-red-700">
          {suggestJob.error || "Couldn't write a fix. Try again."}
        </p>
      )}

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={detailsId}
        className="inline-flex items-center gap-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
      >
        Details
        <span className="sr-only"> for {label}</span>
        {open ? (
          <ChevronUp className="h-3 w-3" aria-hidden="true" />
        ) : (
          <ChevronDown className="h-3 w-3" aria-hidden="true" />
        )}
      </button>

      {open && (
        <div id={detailsId} className="space-y-3 border-t border-border pt-2 text-xs text-foreground">
          {baseline?.rationale && (
            <div>
              <p className="font-semibold text-muted-foreground">Original run</p>
              <p className="mt-0.5 leading-relaxed">{baseline.rationale}</p>
            </div>
          )}
          {current ? (
            <>
              <div>
                <p className="font-semibold text-muted-foreground">Latest replay</p>
                <p className="mt-0.5 leading-relaxed">{current.rationale}</p>
              </div>
              {current.transcript?.length > 0 && (
                <div>
                  <p className="font-semibold text-muted-foreground">Replay transcript</p>
                  <div className="mt-0.5 space-y-1">
                    {current.transcript.map((message, i) => (
                      <p key={i} className="leading-relaxed break-words">
                        <span className="font-medium">
                          {message.role === "user" ? userRoleLabel : agentName}:
                        </span>{" "}
                        {message.content}
                      </p>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <p className="text-muted-foreground">Not replayed yet.</p>
          )}
        </div>
      )}
    </li>
  );
}

// The tests of one criterion: its status, one row per test, and the build step when there are none.
export default function TestsSection({
  criterionSuite,
  jobs,
  agentName,
  userRoleLabel,
  onReplay,
  onBuildTests,
  onSuggestFix,
  onGenerateHeldout,
}) {
  const criterionId = criterionSuite?.criterion_id;
  const tests = criterionSuite?.tests ?? [];
  const buildJob = criterionId ? jobs[`build:${criterionId}`] : undefined;
  const building = isActive(buildJob) || (!buildJob && Boolean(criterionSuite?.active_job_id));
  const heldoutJob = criterionId ? jobs[`heldout:${criterionId}`] : undefined;
  const generating = isActive(heldoutJob) || (!heldoutJob && Boolean(criterionSuite?.heldout_job_id));
  const showSlow = (tests.length === 0 && building) || generating;

  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!showSlow) return undefined;
    const timer = setTimeout(() => setSlow(true), SLOW_REPLY_MS);
    return () => {
      clearTimeout(timer);
      setSlow(false);
    };
  }, [showSlow]);

  if (!criterionSuite) return null;

  const note = lowConfidenceNote(criterionSuite.judge_check);
  const status = criterionSuite.status;
  const onlyFix = tests.length === 1 && tests[0].role === "fix";

  let empty = null;
  if (tests.length === 0) {
    if (building) {
      empty = (
        <div role="status" className="text-sm text-muted-foreground space-y-1">
          <p>Building tests from your saved runs…</p>
          {slow && <p>Still working. The server may be waking up.</p>}
        </div>
      );
    } else if (buildJob?.status === "failed") {
      empty = (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-red-700">
            {buildJob.error || "Couldn't build tests."}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => onBuildTests(criterionId)}>
            Retry
          </Button>
        </div>
      );
    } else {
      empty = (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {buildJob?.result?.already_handled
              ? "None of your saved runs fail this criterion, so there is nothing to test yet."
              : "No tests yet."}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => onBuildTests(criterionId)}>
            Build tests
          </Button>
        </div>
      );
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Tests</p>
        <Badge variant="outline" className={cn("whitespace-nowrap", STATUS_STYLES[status] ?? STATUS_STYLES.untested)}>
          {statusLabel(status)}
        </Badge>
      </div>

      {note && <p className="text-xs text-amber-800">{note}</p>}

      {tests.length > 0 && (
        <ul className="space-y-2">
          {tests.map((test) => (
            <TestRow
              key={test.id}
              test={test}
              job={jobs[`replay:${test.id}`]}
              suggestJob={jobs[`suggest:${test.id}`]}
              agentName={agentName}
              userRoleLabel={userRoleLabel}
              onReplay={onReplay}
              onSuggestFix={onSuggestFix}
            />
          ))}
        </ul>
      )}

      {onlyFix && (
        <p className="text-xs text-muted-foreground">
          No second failing run, so a fix would be verified more weakly.
        </p>
      )}

      {criterionSuite.can_generate_heldout && (
        <div className="space-y-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={generating}
            aria-busy={generating}
            onClick={() => onGenerateHeldout(criterionId)}
          >
            {generating ? "Generating…" : heldoutJob?.status === "failed" ? "Retry" : "Generate a second test"}
            {heldoutJob?.status === "failed" && !generating && <span className="sr-only"> generating a second test</span>}
          </Button>
          {/* Always mounted while the button is offered, so its text is announced when it appears. */}
          <div role="status" className="text-sm text-muted-foreground space-y-1">
            {generating && <p>Writing a second test…</p>}
            {generating && slow && <p>Still working. The server may be waking up.</p>}
            {!generating && heldoutJob?.status === "done" && heldoutJob.result?.created === false && (
              <p>{heldoutJob.result.discarded === true ? HELDOUT_DISCARDED_TEXT : NO_HELDOUT_TEXT}</p>
            )}
          </div>
          {!generating && heldoutJob?.status === "failed" && (
            <p role="alert" className="text-sm text-red-700">
              {heldoutJob.error || "Couldn't generate a second test."}
            </p>
          )}
        </div>
      )}

      {empty}
    </div>
  );
}

import { useEffect } from "react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Diff, Why, OutcomeTable } from "./ChangeReviewParts";
import { useReport } from "../hooks/useReport";
import { reviewStatus, outcomeLine, GAVE_UP_TEXT } from "../lib/reviewStatus";

// The model-written narrative is untrusted: it is rendered only as plain text, and the outcome
// comes from the stored status shown beside it, so the prose never has to be believed.
//
// This component has no live region of its own. It is remounted whenever the candidate's status
// changes, so the panel owns one always-mounted region and the container reports what to announce.

function isActive(job) {
  return job?.status === "queued" || job?.status === "running";
}

function Resolved({ candidate, report, gaveUp, agentName, userRoleLabel, currentNumber }) {
  const outcome = report?.outcome ?? null;
  const promoted = (outcome?.status ?? candidate.status) === "promoted";
  const strength = report?.strength ?? candidate.strength;
  const narrative = outcome?.narrative ?? null;
  const showExplanation = Boolean(outcome) && (narrative || outcome.explanation_unavailable || gaveUp);

  return (
    <div>
      {Array.isArray(outcome?.table) && outcome.table.length > 0 && (
        <OutcomeTable table={outcome.table} agentName={agentName} userRoleLabel={userRoleLabel} />
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge
          variant="outline"
          className={promoted ? "border-green-200 bg-green-50 text-green-800" : "border-red-200 bg-red-50 text-red-700"}
        >
          {promoted ? "Promoted" : "Not promoted"}
        </Badge>
        <p className="text-sm font-medium text-foreground break-words">{outcomeLine(candidate, outcome)}</p>
      </div>
      {/* Past tense: it only says what chat did, and only while that is still the live version. */}
      {!promoted && candidate.parent_number != null && candidate.parent_number === currentNumber && (
        <p className="mt-1 text-sm text-foreground">Chat kept using v{candidate.parent_number}.</p>
      )}
      {strength === "full" && <p className="mt-1 text-xs text-muted-foreground">Verified with a held-out test.</p>}
      {strength === "weaker" && (
        <p className="mt-1 text-xs text-muted-foreground">Verified without a held-out test (weaker).</p>
      )}

      {showExplanation && (
        <div className="mt-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Explanation (written by a model)
          </h4>
          {narrative ? (
            <div className="mt-1 space-y-1 text-sm text-foreground break-words">
              <p>{narrative.explanation}</p>
              <p>
                <span className="font-medium">Next step:</span> {narrative.next_step}
              </p>
            </div>
          ) : (
            <p className="mt-1 text-sm text-muted-foreground">
              {outcome.explanation_unavailable ? "Explanation unavailable." : GAVE_UP_TEXT}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function ChangeReview({
  candidate,
  version,
  report,
  reportLoading,
  reportError,
  busy,
  error,
  gaveUp = false,
  onApprove,
  onReject,
  onRetry,
  agentName = "Agent",
  userRoleLabel = "Customer",
  currentNumber = null,
}) {
  const n = candidate.number;
  const proposed = candidate.status === "proposed";
  const verifying = candidate.status === "approved_verifying";
  const loading = Boolean(reportLoading) && !report && !verifying;
  const ready = Boolean(version) && Boolean(report);
  const { statusText } = reviewStatus({ candidate, report, reportLoading, reportError, gaveUp });

  const rejectButton = (
    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onReject?.()}>
      Reject
    </Button>
  );

  return (
    <section aria-label="Change review" className="rounded-lg border border-border bg-white p-3">
      <h3 tabIndex={-1} data-review-focus="" className="text-sm font-semibold text-foreground focus:outline-none">
        {proposed ? `Suggested fix v${n}` : `What happened with v${n}`}
      </h3>

      {statusText && <p className="mt-1 text-sm text-muted-foreground">{statusText}</p>}

      {reportError && !verifying && proposed && (
        <div className="mt-2 space-y-2">
          <p role="alert" className="text-sm text-red-700">
            Couldn&apos;t load the suggested fix. {reportError}
          </p>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => onRetry?.()}>
              Retry
            </Button>
            {rejectButton}
          </div>
        </div>
      )}

      {reportError && !verifying && !proposed && (
        <div className="mt-2 space-y-2">
          <p role="alert" className="text-sm text-red-700">
            Couldn&apos;t load the explanation. {reportError}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => onRetry?.()}>
            Retry
          </Button>
        </div>
      )}

      {proposed && ready && (
        <>
          <p className="mt-2 text-sm text-muted-foreground">
            Chat keeps using v{candidate.parent_number} until this fix is verified.
          </p>
          <div className="mt-3">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Changes to the prompt</h4>
            <Diff parentText={version.parent_text} text={version.text} />
          </div>
          {report.why && <Why why={report.why} />}

          {error && (
            <p role="alert" className="mt-3 text-sm text-red-700">
              {error}
            </p>
          )}

          <div className="mt-3 flex items-center gap-2">
            <Button type="button" variant="accent" size="sm" disabled={busy} aria-busy={busy} onClick={() => onApprove?.()}>
              Approve and verify
            </Button>
            {rejectButton}
          </div>
        </>
      )}

      {proposed && !ready && error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {!proposed && !verifying && !loading && !reportError && (
        <Resolved
          candidate={candidate}
          report={report}
          gaveUp={gaveUp}
          agentName={agentName}
          userRoleLabel={userRoleLabel}
          currentNumber={currentNumber}
        />
      )}
    </section>
  );
}

// Fetches the report for the candidate and maps the version action's job state onto ChangeReview.
// The panel mounts this with a key that includes the candidate status (and a retry counter), so
// the report is fetched again whenever either changes.
export function ChangeReviewContainer({
  candidate,
  jobs,
  onApprove,
  onReject,
  onRetry,
  onAnnounce,
  agentName,
  userRoleLabel,
  currentNumber,
}) {
  const { report, version, loading, error, gaveUp } = useReport(candidate.version_id, { enabled: true });
  const job = jobs?.[`version:${candidate.version_id}`];
  const { announcement } = reviewStatus({
    candidate,
    report,
    reportLoading: loading,
    reportError: error,
    gaveUp,
  });

  useEffect(() => {
    onAnnounce?.(announcement);
  }, [announcement, onAnnounce]);

  return (
    <ChangeReview
      candidate={candidate}
      version={version}
      report={report}
      reportLoading={loading}
      reportError={error}
      gaveUp={gaveUp}
      busy={isActive(job)}
      error={job?.status === "failed" ? job.error || "That didn't work. Try again." : null}
      onApprove={() => onApprove?.(candidate.version_id)}
      onReject={() => onReject?.(candidate.version_id)}
      onRetry={onRetry}
      agentName={agentName}
      userRoleLabel={userRoleLabel}
      currentNumber={currentNumber}
    />
  );
}

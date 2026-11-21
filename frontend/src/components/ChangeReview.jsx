import { Fragment, useState, useId, useMemo } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { diffLines } from "../lib/diff";
import { verdictLabel } from "../lib/testStatus";
import { useReport } from "../hooks/useReport";
import { cn } from "../lib/utils";

// Everything the model wrote (the why summary, row rationales, the narrative) is untrusted. It is
// only ever rendered as plain React text children: never as markup. The outcome comes from the
// stored status, shown beside the narrative, so the prose never has to be believed.

const ROLE_LABELS = { fix: "Fix test", heldout: "Held-out test", regression: "Previously passing" };

const WHY_FIELDS = [
  { key: "criterion_name", label: "Criterion" },
  { key: "change_summary", label: "The change" },
  { key: "touched_section", label: "Part of the prompt it touches" },
  { key: "expected_effect", label: "Expected effect" },
  { key: "risk", label: "Risk" },
];

const DIFF_PREFIX = { added: "+ ", removed: "- ", same: "  " };
const DIFF_LABEL = { added: "Added line", removed: "Removed line" };
const DIFF_STYLE = { added: "bg-green-50", removed: "bg-red-50", same: "" };

function isActive(job) {
  return job?.status === "queued" || job?.status === "running";
}

function Diff({ parentText, text }) {
  const lines = useMemo(() => diffLines(parentText, text), [parentText, text]);
  return (
    <ul
      role="list"
      aria-label="Prompt changes"
      className="mt-1 max-h-72 overflow-auto rounded-md border border-border bg-white py-1 font-mono text-xs"
    >
      {lines.map((line, i) => (
        <li key={i} className={cn("whitespace-pre-wrap break-words px-2", DIFF_STYLE[line.type])}>
          {DIFF_LABEL[line.type] && <span className="sr-only">{DIFF_LABEL[line.type]}</span>}
          <span aria-hidden="true">{DIFF_PREFIX[line.type]}</span>
          {line.text}
        </li>
      ))}
    </ul>
  );
}

function Why({ why }) {
  return (
    <div className="mt-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Why this change</h4>
      {why.failing_quote && (
        <div className="mt-1">
          <p className="text-xs font-semibold text-muted-foreground">Where it went wrong</p>
          <blockquote className="mt-0.5 border-l-2 border-border pl-2 text-sm text-foreground break-words">
            {why.failing_quote}
          </blockquote>
        </div>
      )}
      <dl className="mt-1 space-y-1">
        {WHY_FIELDS.filter(({ key }) => why[key]).map(({ key, label }) => (
          <div key={key}>
            <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
            <dd className="text-sm text-foreground break-words">{why[key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function OutcomeRow({ row, agentName, userRoleLabel }) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const label = ROLE_LABELS[row.role] ?? "Test";
  const transcript = Array.isArray(row.transcript) ? row.transcript : [];

  return (
    <Fragment>
      <tr className="border-t border-border align-top">
        <td className="py-1.5 pr-2">
          <p className="font-medium text-foreground">{label}</p>
          {row.criterion_name && <p className="text-muted-foreground break-words">{row.criterion_name}</p>}
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={detailsId}
            className="mt-0.5 inline-flex items-center gap-1 rounded text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          >
            Details
            <span className="sr-only"> for {label}</span>
            {open ? (
              <ChevronUp className="h-3 w-3" aria-hidden="true" />
            ) : (
              <ChevronDown className="h-3 w-3" aria-hidden="true" />
            )}
          </button>
        </td>
        <td className="py-1.5 pr-2">{verdictLabel(row.before)}</td>
        <td className="py-1.5 font-medium">{verdictLabel(row.after)}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={3} className="pb-2">
            <div id={detailsId} className="space-y-2 rounded-md bg-muted p-2 text-foreground">
              {row.rationale && (
                <div>
                  <p className="font-semibold text-muted-foreground">Judge&apos;s reasoning</p>
                  <p className="mt-0.5 leading-relaxed break-words">{row.rationale}</p>
                </div>
              )}
              {transcript.length > 0 && (
                <div>
                  <p className="font-semibold text-muted-foreground">Transcript</p>
                  <div className="mt-0.5 space-y-1">
                    {transcript.map((message, i) => (
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
              {!row.rationale && transcript.length === 0 && <p className="text-muted-foreground">No details stored.</p>}
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function OutcomeTable({ table, agentName, userRoleLabel }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">Test results before and after the change</caption>
        <thead>
          <tr className="text-muted-foreground">
            <th scope="col" className="pb-1 pr-2 font-semibold">Test</th>
            <th scope="col" className="pb-1 pr-2 font-semibold">Before</th>
            <th scope="col" className="pb-1 font-semibold">After</th>
          </tr>
        </thead>
        <tbody>
          {table.map((row, i) => (
            <OutcomeRow key={row.test_id ?? i} row={row} agentName={agentName} userRoleLabel={userRoleLabel} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Resolved({ candidate, report, agentName, userRoleLabel }) {
  const outcome = report?.outcome ?? null;
  const status = outcome?.status ?? candidate.status;
  const promoted = status === "promoted";
  const n = candidate.number;
  const strength = report?.strength ?? candidate.strength;
  const narrative = outcome?.narrative ?? null;

  return (
    <div>
      {Array.isArray(outcome?.table) && outcome.table.length > 0 && (
        <OutcomeTable table={outcome.table} agentName={agentName} userRoleLabel={userRoleLabel} />
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant="outline" className={promoted ? "border-green-200 bg-green-50 text-green-800" : "border-red-200 bg-red-50 text-red-700"}>
          {promoted ? "Promoted" : "Not promoted"}
        </Badge>
        <p className="text-sm font-medium text-foreground break-words">
          {promoted
            ? `v${n} is now live.`
            : `v${n} was not promoted${outcome?.reason ? `: ${outcome.reason}` : "."}`}
        </p>
      </div>
      {!promoted && (
        <p className="mt-1 text-sm text-foreground">Chat still uses v{candidate.parent_number}.</p>
      )}
      {strength === "full" && <p className="mt-1 text-xs text-muted-foreground">Verified with a held-out test.</p>}
      {strength === "weaker" && (
        <p className="mt-1 text-xs text-muted-foreground">Verified without a held-out test (weaker).</p>
      )}

      {outcome && (narrative || outcome.explanation_unavailable) && (
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
            <p className="mt-1 text-sm text-muted-foreground">Explanation unavailable.</p>
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
  onApprove,
  onReject,
  agentName = "Agent",
  userRoleLabel = "Customer",
}) {
  const n = candidate.number;
  const proposed = candidate.status === "proposed";
  const verifying = candidate.status === "approved_verifying";
  const outcome = report?.outcome ?? null;
  const loading = Boolean(reportLoading) && !report && !verifying;
  const narrativePending =
    !proposed && !verifying && Boolean(outcome) && !outcome.narrative && !outcome.explanation_unavailable;
  const ready = Boolean(version) && Boolean(report);

  // One live region for the whole review: it exists from the first render, so each of these
  // messages is announced when its text appears.
  let statusText = "";
  if (verifying) {
    const p = candidate.progress;
    statusText = p
      ? `Verifying v${n}: ${p.done} of ${p.total} tests done. Chat still uses v${candidate.parent_number}.`
      : `Verifying v${n}. Chat still uses v${candidate.parent_number}.`;
  } else if (loading) {
    statusText = "Loading the suggested fix…";
  } else if (narrativePending) {
    statusText = "Writing the explanation…";
  }

  return (
    <section aria-label="Change review" className="rounded-lg border border-border bg-white p-3">
      <h3 className="text-sm font-semibold text-foreground">
        {proposed ? `Suggested fix v${n}` : `What happened with v${n}`}
      </h3>

      <div role="status" className="mt-1 text-sm text-muted-foreground">
        {statusText}
      </div>

      {reportError && !verifying && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          Couldn&apos;t load the suggested fix. {reportError}
        </p>
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
            <Button type="button" variant="accent" size="sm" disabled={busy} aria-busy={busy} onClick={() => onApprove()}>
              Approve and verify
            </Button>
            <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onReject()}>
              Reject
            </Button>
          </div>
        </>
      )}

      {proposed && !ready && error && (
        <p role="alert" className="mt-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {!proposed && !verifying && !loading && !reportError && (
        <Resolved candidate={candidate} report={report} agentName={agentName} userRoleLabel={userRoleLabel} />
      )}
    </section>
  );
}

// Fetches the report for the candidate and maps the version action's job state onto ChangeReview.
// The panel mounts this with a key that includes the candidate status, so the report is fetched
// again whenever the status changes.
export function ChangeReviewContainer({ candidate, jobs, onApprove, onReject, agentName, userRoleLabel }) {
  const { report, version, loading, error } = useReport(candidate.version_id, { enabled: true });
  const job = jobs?.[`version:${candidate.version_id}`];

  return (
    <ChangeReview
      candidate={candidate}
      version={version}
      report={report}
      reportLoading={loading}
      reportError={error}
      busy={isActive(job)}
      error={job?.status === "failed" ? job.error || "That didn't work. Try again." : null}
      onApprove={() => onApprove(candidate.version_id)}
      onReject={() => onReject(candidate.version_id)}
      agentName={agentName}
      userRoleLabel={userRoleLabel}
    />
  );
}

import { forwardRef } from "react";
import { Button } from "../ui/button";
import { humanize, shortId } from "./format";

function Tag({ children, tone }) {
  return (
    <span
      className={
        tone === "imported"
          ? "rounded border border-amber-300 bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-amber-900"
          : "rounded border border-border bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground"
      }
    >
      {children}
    </span>
  );
}

function join(values) {
  return values && values.length ? values.map(humanize).join(", ") : "none";
}

// Flagged assistant conversations: ids and labels only. An imported (demo) conversation always
// carries a plain-text "imported" tag.
const FlaggedList = forwardRef(function FlaggedList(
  { items, loading, error, filter, onClearFilter, onReview, onRetry },
  headingRef
) {
  const filterText = filter.section
    ? `section: ${humanize(filter.section)}`
    : filter.intent
      ? `task: ${humanize(filter.intent)}`
      : null;

  return (
    <section aria-labelledby="flagged-heading" className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id="flagged-heading" ref={headingRef} tabIndex={-1} className="text-sm font-semibold text-foreground focus:outline-none">
          Flagged conversations
        </h3>
        {filterText && (
          <>
            <span className="text-sm text-muted-foreground">Filtered by {filterText}</span>
            <Button type="button" size="sm" variant="outline" onClick={onClearFilter}>
              Clear filter
            </Button>
          </>
        )}
      </div>

      <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
        {loading ? "Loading flagged conversations…" : ""}
      </div>
      {error && (
        <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-red-700">
          <p>Couldn&apos;t load flagged conversations. {error}</p>
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
      {!loading && !error && items && items.length === 0 && (
        <p className="text-sm text-muted-foreground">No flagged conversations{filterText ? " for this filter" : ""}.</p>
      )}

      {!error && items && items.length > 0 && (
        <ul className="space-y-2">
          {items.map((run) => (
            <li key={run.run_id} className="rounded-md border border-border bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Tag tone={run.source === "imported" ? "imported" : "live"}>
                  {run.source === "imported" ? "imported" : "live"}
                </Tag>
                <span className="font-mono text-xs text-muted-foreground">{shortId(run.session_id)}</span>
                <span className="text-foreground">{run.patent_number ?? "no patent number"}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="ml-auto"
                  aria-label={`Review in Evolution: conversation ${shortId(run.session_id)}`}
                  onClick={() => onReview(run)}
                >
                  Review in Evolution
                </Button>
              </div>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <dt>Tasks</dt>
                <dd>{join(run.intents)}</dd>
                <dt>Sections</dt>
                <dd>{join(run.sections)}</dd>
                <dt>Reasons</dt>
                <dd>{join(run.reasons)}</dd>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
});

export default FlaggedList;

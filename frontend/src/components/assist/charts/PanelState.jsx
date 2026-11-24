import { Button } from "../../ui/button";

// The four states every console panel has. `what` names the thing that failed to load
// ("quality trends"); the error text is whatever the caller already made safe to show.
export default function PanelState({ state, what = "this panel", error = null, onRetry = null, children = null }) {
  if (state === "loading") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading…
      </p>
    );
  }
  if (state === "empty") {
    return <p className="text-sm text-muted-foreground">Not enough data yet.</p>;
  }
  if (state === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-foreground">
        <p>
          Couldn&apos;t load {what}
          {error ? `: ${error}` : ""}
        </p>
        {onRetry ? (
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
      </div>
    );
  }
  return children;
}

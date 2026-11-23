import { useState } from "react";
import { assistInsights, ApiError } from "../../api";
import { Button } from "../ui/button";

const FAILED = "The insights could not be generated right now. Please try again.";
const NOT_GROUNDED = "Check these figures: some numbers in this summary could not be matched to the data.";

function figureText(value) {
  if (value === null || value === undefined) return "n/a";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// The model-written brief. Everything from the server is rendered as plain text: no markdown, no
// HTML, no links. The brief keeps its line breaks through CSS only.
export default function InsightsBrief() {
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function generate() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      setResult(await assistInsights());
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError && err.message ? err.message : FAILED);
    } finally {
      setPending(false);
    }
  }

  const figures = result ? Object.entries(result.counts_used ?? {}) : [];

  return (
    <section aria-labelledby="insights-heading" className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 id="insights-heading" className="text-sm font-semibold text-foreground">
          Insights brief
        </h3>
        <Button type="button" size="sm" variant="outline" onClick={generate} disabled={pending}>
          Generate insights
        </Button>
      </div>

      <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
        {pending ? "Generating insights…" : ""}
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}

      {result && (
        <div className="space-y-3 rounded-md border border-border bg-white p-4">
          {result.data_source_label && (
            <p className="text-xs font-medium text-amber-900">Based on: {result.data_source_label}</p>
          )}
          {result.grounded === false && (
            <div role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              <p>{NOT_GROUNDED}</p>
              {(result.warnings ?? []).length > 0 && (
                <ul className="mt-1 list-disc pl-5">
                  {result.warnings.map((warning, i) => (
                    <li key={i}>{String(warning)}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <p data-testid="insights-brief" className="whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground">
            {result.brief}
          </p>
          <details>
            <summary className="cursor-pointer text-sm font-medium text-foreground">Figures used</summary>
            <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
              {figures.map(([name, value]) => (
                <li key={name}>
                  {name}: {figureText(value)}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}

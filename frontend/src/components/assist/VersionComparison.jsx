import { humanize, pct } from "./format";

const REAL_ONLY = "Real (non-imported) questions only.";
const TOO_FEW = "too few questions to compare";

// Re-ask and thumbs-down rates per assistant version, from real (non-imported) questions only.
// The table exists only when the server says the comparison is available; otherwise one sentence
// gives the reason. Nothing else feeds this block: no before/after from any other source.
export default function VersionComparison({ comparison }) {
  if (!comparison) return null;
  const versions = comparison.available ? Object.entries(comparison.versions ?? {}) : [];

  return (
    <section aria-labelledby="versions-heading" className="space-y-2">
      <h3 id="versions-heading" className="text-sm font-semibold text-foreground">
        Version comparison
      </h3>
      <p className="text-xs text-muted-foreground">{REAL_ONLY}</p>
      {!comparison.available && (
        <p className="text-sm text-muted-foreground">{comparison.reason}</p>
      )}
      {comparison.available && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Re-ask and thumbs-down rates by assistant version</caption>
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted-foreground">
                <th scope="col" className="py-1 pr-4 font-medium">Version</th>
                <th scope="col" className="py-1 pr-4 font-medium">Questions</th>
                <th scope="col" className="py-1 pr-4 font-medium">Re-ask rate</th>
                <th scope="col" className="py-1 pr-4 font-medium">Thumbs-down rate</th>
              </tr>
            </thead>
            <tbody>
              {versions.map(([number, row]) => {
                const enough = row.enough_data !== false;
                return (
                  <tr
                    key={number}
                    data-enough-data={enough ? "true" : "false"}
                    className={enough ? "" : "text-muted-foreground opacity-60"}
                  >
                    <th scope="row" className="py-1 pr-4 font-medium">v{number}</th>
                    <td className="py-1 pr-4 tabular-nums">{row.questions}</td>
                    {enough ? (
                      <>
                        <td className="py-1 pr-4 tabular-nums">{pct(row.reask_rate)}</td>
                        <td className="py-1 pr-4 tabular-nums">{pct(row.thumbs_down_rate)}</td>
                      </>
                    ) : (
                      <td colSpan={2} className="py-1 pr-4">{TOO_FEW}</td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-muted-foreground">By intent</summary>
            <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
              {versions
                .filter(([, row]) => row.enough_data !== false)
                .flatMap(([number, row]) =>
                  Object.entries(row.intents ?? {}).map(([intent, rates]) => (
                    <li key={`${number}:${intent}`}>
                      v{number} · {humanize(intent)}: {rates.questions} questions, re-ask {pct(rates.reask_rate)}, thumbs-down {pct(rates.thumbs_down_rate)}
                    </li>
                  ))
                )}
            </ul>
          </details>
        </div>
      )}
    </section>
  );
}

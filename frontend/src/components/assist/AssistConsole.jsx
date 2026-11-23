import { useEffect, useRef, useState } from "react";
import { assistMetrics, assistStatus } from "../../api";
import { restoreAssistBackup } from "../../lib/workspace";
import { Button } from "../ui/button";
import AdoptionPanel from "./AdoptionPanel";
import EvolutionPanel from "./EvolutionPanel";

const TABS = [
  { id: "adoption", label: "Adoption" },
  { id: "evolution", label: "Evolution" },
];

function sourceLine(name, counts) {
  if (!counts) return null;
  return `${name} — Live: ${counts.live ?? 0} · Imported: ${counts.imported ?? 0}`;
}

function useConsoleData(reloadKey) {
  const [result, setResult] = useState({ key: null, status: null, metrics: null, error: null });

  useEffect(() => {
    let cancelled = false;
    Promise.all([assistStatus(), assistMetrics()])
      .then(([status, metrics]) => {
        if (!cancelled) setResult({ key: reloadKey, status, metrics, error: null });
      })
      .catch((err) => {
        if (cancelled) return;
        const error = err instanceof Error && err.message ? err.message : "Something went wrong.";
        setResult({ key: reloadKey, status: null, metrics: null, error });
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  // The last good data stays on screen while a refresh is in flight, so the open tab keeps its state.
  return { ...result, loading: result.key !== reloadKey };
}

// The builder-facing console for the Patent Assist agent: how Google Patents is used (Adoption)
// and the improvement loop for the assistant (Evolution). Most of the data is an imported demo
// dataset, and the header says so whenever the API does.
export default function AssistConsole({ onDisconnect }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [tab, setTab] = useState("adoption");
  const [evolutionOpened, setEvolutionOpened] = useState(false);
  const tabRefs = useRef({});
  const data = useConsoleData(reloadKey);
  const { status, metrics } = data;

  function selectTab(id) {
    setTab(id);
    if (id === "evolution") setEvolutionOpened(true);
    tabRefs.current[id]?.focus();
  }

  function onTabKeyDown(e) {
    const index = TABS.findIndex((t) => t.id === tab);
    let next = null;
    if (e.key === "ArrowRight") next = TABS[(index + 1) % TABS.length];
    else if (e.key === "ArrowLeft") next = TABS[(index + TABS.length - 1) % TABS.length];
    else if (e.key === "Home") next = TABS[0];
    else if (e.key === "End") next = TABS[TABS.length - 1];
    if (!next) return;
    e.preventDefault();
    selectTab(next.id);
  }

  function disconnect() {
    restoreAssistBackup();
    onDisconnect();
  }

  const label = metrics?.data_source_label;
  const sources = metrics?.sources;

  return (
    <div className="min-h-dvh bg-background">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-white px-4 py-3 sm:px-6">
        <h1 className="text-base font-semibold tracking-wide text-foreground">Assist console</h1>
        {status && (
          <span className="text-xs text-muted-foreground">
            {status.agent_name}
            {status.current_version_number != null ? ` · v${status.current_version_number}` : ""}
            {` · ${status.run_count} conversations · ${status.flagged_run_count} flagged · ${status.imported_run_count} imported`}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => setReloadKey((n) => n + 1)} disabled={data.loading}>
            Refresh data
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={disconnect}>
            Disconnect
          </Button>
        </div>
      </header>

      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6">
        {label && (
          <div role="note" className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <p>
              <span className="font-semibold">Data source: {label}.</span> Most of these figures come from an
              imported dataset, not measured production usage.
            </p>
          </div>
        )}
        {sources && (
          <p className="mb-4 text-xs text-muted-foreground">
            {[sourceLine("Page views", sources.page_views), sourceLine("Questions", sources.questions)]
              .filter(Boolean)
              .join("  |  ")}
          </p>
        )}

        <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {data.loading ? "Loading the console…" : ""}
        </div>
        {data.error && (
          <p role="alert" className="mb-4 text-sm text-red-700">
            Couldn&apos;t load the console: {data.error}
          </p>
        )}

        {!data.error && status && metrics && (
          <>
            <div role="tablist" aria-label="Assist console" onKeyDown={onTabKeyDown} className="mb-6 flex gap-1 border-b border-border">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  ref={(el) => {
                    tabRefs.current[t.id] = el;
                  }}
                  type="button"
                  role="tab"
                  id={`assist-tab-${t.id}`}
                  aria-selected={tab === t.id}
                  aria-controls={`assist-panel-${t.id}`}
                  tabIndex={tab === t.id ? 0 : -1}
                  onClick={() => selectTab(t.id)}
                  className={
                    tab === t.id
                      ? "border-b-2 border-foreground px-4 py-2 text-sm font-medium text-foreground"
                      : "border-b-2 border-transparent px-4 py-2 text-sm text-muted-foreground hover:text-foreground"
                  }
                >
                  {t.label}
                </button>
              ))}
            </div>

            <div role="tabpanel" id="assist-panel-adoption" aria-labelledby="assist-tab-adoption" hidden={tab !== "adoption"}>
              <AdoptionPanel metrics={metrics} reloadKey={reloadKey} onReview={() => selectTab("evolution")} />
            </div>
            <div role="tabpanel" id="assist-panel-evolution" aria-labelledby="assist-tab-evolution" hidden={tab !== "evolution"}>
              {evolutionOpened && <EvolutionPanel agentId={status.agent_id} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

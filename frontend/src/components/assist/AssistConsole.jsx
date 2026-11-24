import { useEffect, useRef, useState } from "react";
import { assistMetrics, assistOverview, assistStatus } from "../../api";
import { useConsoleQuery } from "../../hooks/useConsoleQuery";
import { restoreAssistBackup } from "../../lib/workspace";
import { Button } from "../ui/button";
import AdoptionPanel from "./AdoptionPanel";
import ConsoleControls from "./ConsoleControls";
import { DEFAULT_FILTERS } from "./consoleFilters";
import EvolutionPanel from "./EvolutionPanel";
import OverviewTab from "./OverviewTab";
import RefreshFrame from "./RefreshFrame";

// Overview is first and the default. Every other tab mounts the first time it is opened and then stays
// mounted (hidden) so it keeps its state. Each tab component receives `{ filters, reloadKey, onOpenTab }`.
const TABS = [
  { id: "overview", label: "Overview" },
  { id: "adoption", label: "Adoption" },
  { id: "quality", label: "Quality" },
  { id: "evolution", label: "Evolution" },
  { id: "operations", label: "Operations" },
];

const ANCHOR_TRIES = 10;
const ANCHOR_RETRY_MS = 200;

// The status call ignores the filters, so it gets a constant set and never refetches because of them.
const STATUS_FILTERS = { range: "all", source: "all", version: null };
const fetchStatus = () => assistStatus();
const never = () => false;
// The version list remembers which source it was fetched for, so a list is never shown for another one.
const fetchVersions = (filters) =>
  assistOverview(filters).then((overview) => ({ source: filters.source, versions: overview?.versions ?? [] }));

function sourceLine(name, counts) {
  if (!counts) return null;
  return `${name} — Live: ${counts.live ?? 0} · Imported: ${counts.imported ?? 0}`;
}

// Stands in until the Quality and Operations tabs are built.
function Placeholder() {
  return <p className="text-sm text-muted-foreground">Coming in the next task</p>;
}

// The builder-facing console for the Patent Assist agent: an Overview with alerts and headline numbers,
// how Google Patents is used (Adoption), conversation quality (Quality), the improvement loop for the
// assistant (Evolution) and operations. Most of the data can be an imported demo dataset, and the
// controls always say which data is on screen.
export default function AssistConsole({ onDisconnect, autoFocus = false }) {
  const [reloadKey, setReloadKey] = useState(0);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [tab, setTab] = useState("overview");
  const [opened, setOpened] = useState({ overview: true });
  const [anchorRequest, setAnchorRequest] = useState(null);
  const tabRefs = useRef({});
  const headingRef = useRef(null);

  const statusQuery = useConsoleQuery(fetchStatus, STATUS_FILTERS, reloadKey, { isEmpty: never });
  const metricsQuery = useConsoleQuery(assistMetrics, filters, reloadKey, { isEmpty: never });
  // The version select's options come from the overview and depend on the source only, so this asks
  // with the cheapest range and no version: it refetches for a new source or a Refresh, not for the rest.
  const versionsQuery = useConsoleQuery(
    fetchVersions,
    { range: "24h", source: filters.source, version: null },
    reloadKey,
    { isEmpty: never }
  );

  const status = statusQuery.state === "error" ? null : statusQuery.data;
  const metrics = metricsQuery.data;
  // Offer the list only if it was fetched for the current source and the last call worked; otherwise
  // the select has just "All" and the selected version, so it never offers a wrong version.
  const versionsFailed = versionsQuery.state === "error";
  const versions =
    !versionsFailed && versionsQuery.data?.source === filters.source ? versionsQuery.data.versions : [];
  const loading = [statusQuery, metricsQuery, versionsQuery].some((q) => q.state === "loading" || q.refreshing);

  // After connecting, focus moves to the console heading (the connect box that had it is gone).
  useEffect(() => {
    if (autoFocus) headingRef.current?.focus();
  }, [autoFocus]);

  // Scroll to the element an alert pointed at. The tab may still be loading, so look again for a moment.
  useEffect(() => {
    const anchor = anchorRequest?.anchor;
    if (!anchor) return undefined;
    let tries = 0;
    let timer;
    function attempt() {
      const target = document.getElementById(anchor);
      if (target) {
        target.scrollIntoView?.({ block: "start" });
        return;
      }
      tries += 1;
      if (tries < ANCHOR_TRIES) timer = setTimeout(attempt, ANCHOR_RETRY_MS);
    }
    attempt();
    return () => clearTimeout(timer);
  }, [anchorRequest]);

  function selectTab(id) {
    setTab(id);
    setOpened((o) => (o[id] ? o : { ...o, [id]: true }));
    tabRefs.current[id]?.focus();
  }

  // How a tab sends the builder somewhere else: switch to `id` and, if given, scroll to the element
  // with that id once it exists.
  function openTab(id, anchor) {
    if (!TABS.some((t) => t.id === id)) return;
    selectTab(id);
    setAnchorRequest((prev) => (anchor ? { tab: id, anchor, n: (prev?.n ?? 0) + 1 } : null));
  }

  // A version number means something only within one source, so a new source starts with all versions.
  function changeFilters(next) {
    setFilters((prev) => ({ ...next, version: next.source === prev.source ? next.version : null }));
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
  const tabProps = { filters, reloadKey, onOpenTab: openTab };

  return (
    <div className="min-h-dvh bg-background">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-white px-4 py-3 sm:px-6">
        <h1 ref={headingRef} tabIndex={-1} className="text-base font-semibold tracking-wide text-foreground focus:outline-none">
          Assist console
        </h1>
        {status && (
          <span className="text-xs text-muted-foreground">
            {status.agent_name}
            {status.current_version_number != null ? ` · v${status.current_version_number}` : ""}
            {` · ${status.run_count} conversations · ${status.flagged_run_count} flagged · ${status.imported_run_count} imported`}
          </span>
        )}
        <div className="ml-auto flex gap-2">
          <Button type="button" variant="outline" size="sm" onClick={disconnect}>
            Disconnect
          </Button>
        </div>
      </header>

      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-6">
        <div className="mb-4">
          <ConsoleControls
            value={filters}
            onChange={changeFilters}
            versions={versions}
            onRefresh={() => setReloadKey((n) => n + 1)}
            loading={loading}
            versionsFailed={versionsFailed}
            onRetryVersions={versionsQuery.retry}
          />
        </div>
        <RefreshFrame refreshing={metricsQuery.refreshing}>
          {label && (
            <div role="note" className="mb-4 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              <p>
                <span className="font-semibold">Data source: {label}.</span> Includes imported demo data, which is
                not measured production usage.
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
        </RefreshFrame>

        <div role="status" aria-live="polite" className="text-sm text-muted-foreground">
          {statusQuery.state === "loading" || statusQuery.refreshing ? "Loading the console…" : ""}
        </div>
        {statusQuery.state === "error" && (
          <p role="alert" className="mb-4 text-sm text-red-700">
            Couldn&apos;t load the console: {statusQuery.error}
          </p>
        )}

        {status && (
          <>
            <div
              role="tablist"
              aria-label="Assist console"
              onKeyDown={onTabKeyDown}
              className="mb-6 flex gap-1 overflow-x-auto border-b border-border"
            >
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
                      ? "shrink-0 whitespace-nowrap border-b-2 border-foreground px-3 py-2 text-sm font-medium text-foreground sm:px-4"
                      : "shrink-0 whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground hover:text-foreground sm:px-4"
                  }
                >
                  {t.label}
                </button>
              ))}
            </div>

            <div role="tabpanel" id="assist-panel-overview" aria-labelledby="assist-tab-overview" hidden={tab !== "overview"}>
              <OverviewTab {...tabProps} />
            </div>
            <div role="tabpanel" id="assist-panel-adoption" aria-labelledby="assist-tab-adoption" hidden={tab !== "adoption"}>
              {opened.adoption && <AdoptionPanel {...tabProps} />}
            </div>
            <div role="tabpanel" id="assist-panel-quality" aria-labelledby="assist-tab-quality" hidden={tab !== "quality"}>
              {opened.quality && <Placeholder />}
            </div>
            <div role="tabpanel" id="assist-panel-evolution" aria-labelledby="assist-tab-evolution" hidden={tab !== "evolution"}>
              {opened.evolution && <EvolutionPanel {...tabProps} agentId={status.agent_id} />}
            </div>
            <div role="tabpanel" id="assist-panel-operations" aria-labelledby="assist-tab-operations" hidden={tab !== "operations"}>
              {opened.operations && <Placeholder />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

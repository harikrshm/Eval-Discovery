import { useState, useEffect } from "react";
import { Bot, History, ListChecks } from "lucide-react";
import { useSession } from "./hooks/useSession";
import { useTests } from "./hooks/useTests";
import { getAgentId } from "./lib/workspace";
import { cn } from "./lib/utils";
import AgentSetup from "./components/AgentSetup";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalCriteriaPanel from "./components/EvalCriteriaPanel";
import HistoryPanel from "./components/HistoryPanel";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";

const PANEL_WIDTH = "min(24rem, 100vw)";
const isNarrow = () => window.matchMedia("(max-width: 1279px)").matches;

// Status badge config keyed by patternState + evalCriteria.length
// "New pattern found" when criteria already exist (evalCriteriaCount > 0), "Pattern found" on first discovery
function PatternBadge({ patternState, criteriaError, evalCriteriaCount }) {
  if (patternState === "idle" || patternState === "pattern_not_found") return null;

  if (patternState === "checking") {
    return (
      <Badge variant="outline" className="bg-muted text-muted-foreground border-0 whitespace-nowrap motion-safe:animate-pulse">
        Checking for patterns…
      </Badge>
    );
  }
  if (patternState === "pattern_found") {
    return (
      <Badge variant="outline" className="bg-green-50 text-green-800 border border-green-200 whitespace-nowrap">
        {evalCriteriaCount === 0 ? "Pattern found" : "New pattern found"}
      </Badge>
    );
  }
  if (patternState === "generating") {
    return (
      <Badge variant="outline" className="bg-amber-50 text-amber-800 border border-amber-200 whitespace-nowrap motion-safe:animate-pulse">
        Generating eval…
      </Badge>
    );
  }
  if (patternState === "error") {
    let msg = "Pattern check failed — retrying automatically";
    if (criteriaError !== null) msg = "Eval generation failed — retry";
    return (
      <Badge variant="outline" className="bg-red-50 text-red-700 border border-red-200 whitespace-nowrap">
        {msg}
      </Badge>
    );
  }
  return null;
}

export default function App() {
  // Read once at startup; after that it only changes when an agent is created.
  // A deleted agent is cleared from storage by the hook, so re-reading storage
  // here would retrigger the load.
  const [agentId, setActiveAgentId] = useState(() => getAgentId());
  const [changingAgent, setChangingAgent] = useState(false);
  const {
    loading,
    loadError,
    loadErrorGone,
    retry,
    agent,
    transcripts,
    currentTranscript,
    evalCriteria,
    patternState,
    patternSummary,
    criteriaSaveError,
    runSaveFailed,
    runSaveMessage,
    addMessage,
    discardDraft,
    saveRun,
    discover,
    addCriteria,
    updateCriteria,
    savePrompt,
  } = useSession(agentId);
  const tests = useTests(agentId);

  const [criteriaError, setCriteriaError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [panelVisible, setPanelVisible] = useState(false);
  const [historyVisible, setHistoryVisible] = useState(false);

  const historyOpen = historyVisible && transcripts.length > 0;
  const evalOpen = panelVisible && evalCriteria.length > 0;

  // Reset criteriaError whenever a new pattern check starts
  useEffect(() => {
    if (patternState === "checking") {
      setCriteriaError(null);
    }
  }, [patternState]);

  // Below the xl breakpoint panels are overlay drawers, so Escape closes them.
  useEffect(() => {
    if (!historyOpen && !evalOpen) return;
    function onKeyDown(e) {
      if (e.key === "Escape" && isNarrow()) {
        setHistoryVisible(false);
        setPanelVisible(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [historyOpen, evalOpen]);

  function openHistory() {
    if (isNarrow()) setPanelVisible(false);
    setHistoryVisible(true);
  }

  function openEvalPanel() {
    if (isNarrow()) setHistoryVisible(false);
    setPanelVisible(true);
  }

  async function handleSaveRun() {
    if (saving) return;
    setSaving(true);
    try {
      await saveRun();
    } finally {
      setSaving(false);
    }
  }

  async function handleDiscoverEvals() {
    if (!patternSummary) return;
    setCriteriaError(null);
    try {
      await discover();
      // The server starts building tests for the new criterion; the refresh picks that job up.
      tests.refresh();
      openEvalPanel();
    } catch (err) {
      setCriteriaError(err.message);
    }
  }

  function handleExportAll() {
    const blob = new Blob([JSON.stringify(evalCriteria, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "eval-criteria.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  async function handleAddCriteria() {
    openEvalPanel();
    const created = await addCriteria({
      name: "New criterion",
      definition: "",
      good_example: "",
      bad_example: "",
    });
    // The suite learns about the new criterion so it can offer "Build tests".
    if (created) tests.refresh();
  }

  async function handleUpdateCriteria(id, fields) {
    await updateCriteria(id, fields);
    // Editing what a criterion means makes its test results out of date.
    if ("definition" in fields || "good_example" in fields || "bad_example" in fields) {
      tests.refresh();
    }
  }

  async function handleSavePrompt(text) {
    await savePrompt(text);
    tests.refresh();
  }

  function handleDataDeleted() {
    // Back to the first-run state: no agent, no stored token, clean UI.
    setHistoryVisible(false);
    setPanelVisible(false);
    setCriteriaError(null);
    setSaving(false);
    setChangingAgent(false);
    setActiveAgentId(null);
  }

  function handleAgentCreated(created) {
    // Start the new agent from a clean UI; the hook hydrates its server state.
    setHistoryVisible(false);
    setPanelVisible(false);
    setCriteriaError(null);
    setSaving(false);
    setChangingAgent(false);
    setActiveAgentId(created.id);
  }

  if (agentId == null) {
    return <AgentSetup onCreated={handleAgentCreated} onDeleted={handleDataDeleted} />;
  }

  if (loadError && loadErrorGone) {
    // The stored agent is gone (404/401): pick or create another one.
    return (
      <AgentSetup
        onCreated={handleAgentCreated}
        onDeleted={handleDataDeleted}
        notice={`Couldn't load your agent: ${loadError}`}
      />
    );
  }

  if (loadError) {
    // Transient failure: keep the stored agent and let the user try again.
    return (
      <div className="flex h-dvh items-center justify-center bg-background px-4">
        <div role="alert" className="max-w-md text-center text-sm text-red-700">
          <p>Couldn&apos;t load your agent: {loadError}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={retry}>
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (changingAgent) {
    return (
      <AgentSetup
        onCreated={handleAgentCreated}
        onDeleted={handleDataDeleted}
        onCancel={() => setChangingAgent(false)}
      />
    );
  }

  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center bg-background px-4">
        <p role="status" className="text-muted-foreground">Loading…</p>
      </div>
    );
  }

  const isGenerating = patternState === "generating";
  const showDiscoverButton =
    patternState === "pattern_found" ||
    (patternState === "error" && criteriaError !== null);

  // Only one action is filled at a time: the next step in the loop.
  const endIsPrimary = !showDiscoverButton && !evalOpen;

  return (
    <div className="flex flex-col h-dvh bg-background">

      {/* ── Top bar ── */}
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 sm:px-5 py-2 border-b border-border flex-shrink-0 bg-white">
        <Button
          variant="outline"
          size="sm"
          onClick={openHistory}
          disabled={transcripts.length === 0}
          aria-expanded={historyOpen}
          aria-controls="history-panel"
        >
          <History className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only sm:not-sr-only">History</span>
        </Button>

        <div className="min-w-0 flex items-baseline gap-2">
          <h1 className="text-base font-semibold tracking-wide text-foreground whitespace-nowrap">Eval Discovery</h1>
          <span className="hidden sm:inline min-w-0 truncate text-xs text-muted-foreground whitespace-nowrap">· {agent.name}</span>
        </div>

        <Button variant="outline" size="sm" onClick={() => setChangingAgent(true)} aria-label="Change agent">
          <Bot className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only sm:not-sr-only">Agent</span>
        </Button>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <div role="status">
            <PatternBadge patternState={patternState} criteriaError={criteriaError} evalCriteriaCount={evalCriteria.length} />
            {runSaveFailed && (
              <Badge variant="outline" className="bg-red-50 text-red-700 border border-red-200 whitespace-normal text-left">
                {runSaveMessage}
              </Badge>
            )}
          </div>

          {showDiscoverButton && (
            <Button
              variant="accent"
              size="sm"
              onClick={handleDiscoverEvals}
              disabled={isGenerating}
            >
              Discover evals
            </Button>
          )}

          {evalCriteria.length > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={openEvalPanel}
              aria-expanded={evalOpen}
              aria-controls="eval-panel"
            >
              <ListChecks className="h-4 w-4" aria-hidden="true" />
              <span>
                <span className="sr-only sm:not-sr-only">Eval criteria </span>
                ({evalCriteria.length})
              </span>
            </Button>
          )}
        </div>
      </header>

      {/* ── Body ── */}
      <div className="relative flex flex-1 min-h-0 overflow-hidden">

        {/* Scrim: below lg the panels are overlay drawers */}
        {(historyOpen || evalOpen) && (
          <div
            aria-hidden="true"
            className="xl:hidden absolute inset-0 z-10 bg-black/30"
            onClick={() => {
              setHistoryVisible(false);
              setPanelVisible(false);
            }}
          />
        )}

        {/* History panel — always mounted (keeps the width animation), inert while closed */}
        <aside
          id="history-panel"
          aria-label="Run history"
          inert={!historyOpen}
          style={{ width: historyOpen ? PANEL_WIDTH : 0 }}
          className={cn(
            "flex-shrink-0 overflow-hidden bg-[#fafafa] motion-safe:transition-[width] motion-safe:duration-200",
            "max-xl:absolute max-xl:inset-y-0 max-xl:left-0 max-xl:z-20",
            historyOpen && "border-r border-border max-xl:shadow-xl"
          )}
        >
          <div className="h-full" style={{ width: PANEL_WIDTH }}>
            <HistoryPanel
              agent={agent}
              transcripts={transcripts}
              onDismiss={() => setHistoryVisible(false)}
            />
          </div>
        </aside>

        {/* Chat column */}
        <main className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <ChatInterface
            key={agentId}
            agent={agent}
            agentId={agentId}
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            onDiscard={discardDraft}
            disabled={isGenerating}
          />
        </main>

        {/* Eval criteria panel — always mounted, inert while closed */}
        <aside
          id="eval-panel"
          aria-label="Eval criteria"
          inert={!evalOpen}
          style={{ width: evalOpen ? PANEL_WIDTH : 0 }}
          className={cn(
            "flex-shrink-0 overflow-hidden bg-[#fafafa] motion-safe:transition-[width] motion-safe:duration-200",
            "max-xl:absolute max-xl:inset-y-0 max-xl:right-0 max-xl:z-20",
            evalOpen && "border-l border-border max-xl:shadow-xl"
          )}
        >
          {evalCriteria.length > 0 && (
            <div className="h-full" style={{ width: PANEL_WIDTH }}>
              <EvalCriteriaPanel
                evalCriteria={evalCriteria}
                patternState={patternState}
                discoverPending={showDiscoverButton}
                onDismiss={() => setPanelVisible(false)}
                onUpdate={handleUpdateCriteria}
                onAdd={handleAddCriteria}
                saveError={criteriaSaveError}
                onExportAll={handleExportAll}
                suite={tests.suite}
                jobs={tests.jobs}
                agentName={agent.name}
                userRoleLabel={agent.user_role_label}
                onReplay={tests.replay}
                onBuildTests={tests.buildTests}
                onRunAll={tests.runAll}
                promptEditor={{
                  promptText: agent.system_prompt,
                  versionNumber: agent.prompt_version_number,
                  onSave: handleSavePrompt,
                }}
              />
            </div>
          )}
        </aside>
      </div>

      {/* ── Bottom bar ── */}
      <RunControls
        currentTranscriptLength={currentTranscript.length}
        onSaveRun={handleSaveRun}
        saving={saving}
        primary={endIsPrimary}
      />
    </div>
  );
}

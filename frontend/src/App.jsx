import { useState, useEffect, useRef } from "react";
import { Bot, History, ListChecks, Gauge } from "lucide-react";
import { useEvalLoop } from "./hooks/useEvalLoop";
import { applyStorageEvent, getAgentId, isAssistConnected, recoverInterruptedSwap } from "./lib/workspace";
import { readiness } from "./lib/readiness";
import { cn } from "./lib/utils";
import AgentSetup from "./components/AgentSetup";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalCriteriaPanel from "./components/EvalCriteriaPanel";
import HistoryPanel from "./components/HistoryPanel";
import ReadinessChip from "./components/ReadinessChip";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import ConnectBox from "./components/assist/ConnectBox";
import ConsoleLoader from "./components/assist/ConsoleLoader";

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

function MainApp({ headerAction = null, onSettled = undefined }) {
  // Read once at startup; after that it only changes when an agent is created.
  // A deleted agent is cleared from storage by the hook, so re-reading storage
  // here would retrigger the load.
  const [agentId, setActiveAgentId] = useState(() => getAgentId());
  const [changingAgent, setChangingAgent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [panelVisible, setPanelVisible] = useState(false);
  const [historyVisible, setHistoryVisible] = useState(false);

  function openEvalPanel() {
    if (isNarrow()) setHistoryVisible(false);
    setPanelVisible(true);
  }

  const loop = useEvalLoop(agentId, { onOpenPanel: openEvalPanel });
  const { tests, criteriaError, setCriteriaError, panelProps } = loop;
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
    runSaveFailed,
    runSaveMessage,
    addMessage,
    discardDraft,
    saveRun,
  } = loop.session;

  // Tell the parent when loading has finished (successfully or not); its entry button, if any, has
  // been mounted by then.
  useEffect(() => {
    if (!loading) onSettled?.();
  }, [loading, loadError, onSettled]);

  const historyOpen = historyVisible && transcripts.length > 0;
  const evalOpen = panelVisible && evalCriteria.length > 0;

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

  async function handleSaveRun() {
    if (saving) return;
    setSaving(true);
    try {
      await saveRun();
    } finally {
      setSaving(false);
    }
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
    return <AgentSetup onCreated={handleAgentCreated} onDeleted={handleDataDeleted} headerAction={headerAction} />;
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
        headerAction={headerAction}
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
          {headerAction}
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
              onClick={() => loop.discoverEvals()}
              disabled={isGenerating}
            >
              Discover evals
            </Button>
          )}

          {/* The live chip: always mounted (empty without criteria or a suite) so its first text is
              announced, and outside the panel, which is inert while closed. */}
          <ReadinessChip readiness={evalCriteria.length > 0 && tests.suite ? readiness(tests.suite) : null} />

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
                key={agentId}
                {...panelProps}
                discoverPending={showDiscoverButton}
                onDismiss={() => setPanelVisible(false)}
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

// Connecting to the Assist console swaps the app over to the demo workspace, so while connected
// the console is the whole screen (the chat app, with its "delete my data" control, is not
// reachable against the demo workspace). Disconnecting restores the stored workspace and agent,
// and the app mounts fresh against them. Storage is shared by every tab of the browser, so a
// change of the connected state in another tab (the `storage` event) moves this tab with it.
export default function App() {
  const [connected, setConnected] = useState(() => {
    // An interrupted swap (a backup without the marker) is undone before anything reads the token.
    recoverInterruptedSwap();
    return isAssistConnected();
  });
  const [connectOpen, setConnectOpen] = useState(false);
  const [justConnected, setJustConnected] = useState(false);
  const entryRef = useRef(null);
  const focusEntry = useRef(false);

  const connectedRef = useRef(connected);
  useEffect(() => {
    connectedRef.current = connected;
  }, [connected]);

  useEffect(() => {
    function onStorage(event) {
      // Only localStorage carries the connected state; a sessionStorage event is not ours to act on.
      try {
        if (event.storageArea !== window.localStorage) return;
      } catch {
        return;
      }
      applyStorageEvent(event);
      const now = isAssistConnected();
      if (now === connectedRef.current) return;
      connectedRef.current = now;
      setConnectOpen(false);
      setJustConnected(false);
      setConnected(now);
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  if (connected) {
    return (
      <ConsoleLoader
        autoFocus={justConnected}
        onDisconnect={() => {
          focusEntry.current = true;
          setJustConnected(false);
          setConnected(false);
        }}
      />
    );
  }

  const entry = (
    <Button
      ref={(el) => {
        entryRef.current = el;
        // After a disconnect, focus lands on this button once the app has mounted its header.
        if (el && focusEntry.current) {
          focusEntry.current = false;
          el.focus();
        }
      }}
      variant="outline"
      size="sm"
      onClick={() => setConnectOpen((open) => !open)}
      aria-haspopup="dialog"
      aria-expanded={connectOpen}
    >
      <Gauge className="h-4 w-4" aria-hidden="true" />
      <span>Assist console</span>
    </Button>
  );

  return (
    <>
      <MainApp
        headerAction={entry}
        onSettled={() => {
          // The restored app has finished loading. If the entry button is still not on screen, the
          // pending focus request will not be met, so it must not fire when the button appears later.
          if (focusEntry.current && !entryRef.current) focusEntry.current = false;
        }}
      />
      {connectOpen && (
        <ConnectBox
          returnFocusRef={entryRef}
          onClose={() => setConnectOpen(false)}
          onConnected={() => {
            setConnectOpen(false);
            setJustConnected(true);
            setConnected(true);
          }}
        />
      )}
    </>
  );
}

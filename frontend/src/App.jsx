import { useState, useEffect } from "react";
import { useSession } from "./hooks/useSession";
import { generateCriteria } from "./api";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalCriteriaPanel from "./components/EvalCriteriaPanel";
import HistoryPanel from "./components/HistoryPanel";
import { Badge } from "./components/ui/badge";

// Status badge config keyed by patternState + evalCriteria.length
// "New pattern found" when criteria already exist (evalCriteriaCount > 0), "Pattern found" on first discovery
function PatternBadge({ patternState, criteriaError, evalCriteriaCount }) {
  if (patternState === "idle" || patternState === "pattern_not_found") return null;

  if (patternState === "checking") {
    return (
      <Badge variant="outline" className="bg-muted text-muted-foreground border-0 animate-pulse">
        Checking for patterns…
      </Badge>
    );
  }
  if (patternState === "pattern_found") {
    return (
      <Badge variant="outline" className="bg-green-50 text-green-700 border border-green-200">
        {evalCriteriaCount === 0 ? "Pattern found" : "New pattern found"}
      </Badge>
    );
  }
  if (patternState === "generating") {
    return (
      <Badge variant="outline" className="bg-amber-50 text-amber-700 border border-amber-200 animate-pulse">
        Generating eval…
      </Badge>
    );
  }
  if (patternState === "error") {
    const msg =
      criteriaError !== null
        ? "Eval generation failed — retry"
        : "Pattern check failed — retrying automatically";
    return (
      <Badge variant="outline" className="bg-red-50 text-red-600 border border-red-200">
        {msg}
      </Badge>
    );
  }
  return null;
}

export default function App() {
  const {
    runCount,
    transcripts,
    currentTranscript,
    evalCriteria,
    patternState,
    patternSummary,
    lastCheckedAtRun,
    addMessage,
    saveRun,
    addCriteria,
    updateCriteria,
    setPatternState,
  } = useSession();

  const [criteriaError, setCriteriaError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [panelVisible, setPanelVisible] = useState(false);
  const [historyVisible, setHistoryVisible] = useState(false);

  // On mount: if session was reloaded into "error" with a valid patternSummary, the error
  // came from criteria generation (criteriaError is not persisted). Reset to "pattern_found"
  // so the "Discover Evals" button reappears instead of showing the misleading
  // "Pattern check failed" message.
  useEffect(() => {
    if (patternState === "error" && patternSummary) {
      setPatternState("pattern_found");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // intentionally runs once on mount only

  // Reset criteriaError whenever a new pattern check starts
  useEffect(() => {
    if (patternState === "checking") {
      setCriteriaError(null);
    }
  }, [patternState]);

  async function handleSaveRun() {
    if (saving) return;
    setSaving(true);
    try {
      await saveRun({
        currentPatternState: patternState,
        currentTranscripts: transcripts,
        currentRunCount: runCount,
        currentCurrentTranscript: currentTranscript,
        currentLastCheckedAtRun: lastCheckedAtRun,
      });
    } finally {
      setSaving(false);
    }
  }

  async function handleDiscoverEvals() {
    if (!patternSummary) return;
    setCriteriaError(null);
    setPatternState("generating");
    try {
      const result = await generateCriteria(patternSummary);
      addCriteria({ id: crypto.randomUUID(), ...result });
      setPanelVisible(true);
      setPatternState("complete");
    } catch (err) {
      setCriteriaError(err.message);
      setPatternState("error");
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

  function handleAddCriteria() {
    addCriteria({
      id: crypto.randomUUID(),
      name: "New Criterion",
      definition: "",
      good_example: "",
      bad_example: "",
      why_discovered: "(manually added)",
    });
    setPanelVisible(true);
  }

  const isGenerating = patternState === "generating";
  const showDiscoverButton =
    patternState === "pattern_found" ||
    (patternState === "error" && criteriaError !== null);

  return (
    <div className="flex flex-col h-screen bg-background font-['-apple-system,BlinkMacSystemFont,Inter,sans-serif']">

      {/* ── Top Bar (44px) ── */}
      <header className="h-11 flex items-center px-5 border-b border-border flex-shrink-0 bg-white">
        {/* Left: history button */}
        <div className="flex items-center gap-1 text-sm w-40">
          {transcripts.length > 0 ? (
            <button
              onClick={() => setHistoryVisible(true)}
              className="text-sm font-medium text-foreground hover:text-foreground/70 cursor-pointer"
            >
              History
            </button>
          ) : (
            <span className="text-sm font-medium text-muted-foreground/50">History</span>
          )}
        </div>

        {/* Center: title */}
        <div className="flex-1 text-center">
          <span className="text-sm font-semibold tracking-wide text-foreground">Eval Discovery</span>
          <span className="text-xs text-muted-foreground ml-2">· Alex / CloudSync SDR</span>
        </div>

        {/* Right: badges + buttons */}
        <div className="flex items-center gap-2 w-40 justify-end">
          <PatternBadge patternState={patternState} criteriaError={criteriaError} evalCriteriaCount={evalCriteria.length} />

          {showDiscoverButton && (
            <button
              onClick={handleDiscoverEvals}
              disabled={patternState === "generating"}
              className="bg-black text-white hover:bg-black/90 text-xs font-semibold px-3 py-1 rounded-md disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Discover Evals
            </button>
          )}

          {evalCriteria.length > 0 && (
            <button
              onClick={() => setPanelVisible(true)}
              className="inline-flex items-center bg-black text-white hover:bg-black/90 rounded-full px-2.5 py-0.5 text-xs font-semibold"
            >
              Eval Criteria ({evalCriteria.length})
            </button>
          )}

          <button
            className="h-7 w-7 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
            aria-label="More options (reserved)"
            tabIndex="-1"
            disabled
          >
            ···
          </button>
        </div>
      </header>

      {/* ── Body ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* History panel — always mounted, width-animated */}
        <div
          className="flex-shrink-0 overflow-hidden border-r border-border bg-[#fafafa] transition-[width] duration-200"
          style={{ width: historyVisible && transcripts.length > 0 ? 384 : 0 }}
        >
          <div className="w-96 h-full">
            <HistoryPanel
              transcripts={transcripts}
              onDismiss={() => setHistoryVisible(false)}
            />
          </div>
        </div>

        {/* Chat column */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={isGenerating}
          />
        </div>

        {/* Eval criteria panel — always mounted so CSS width transition animates on first reveal */}
        <div
          className="flex-shrink-0 overflow-hidden border-l border-border bg-[#fafafa] transition-[width] duration-200"
          style={{ width: panelVisible && evalCriteria.length > 0 ? 384 : 0 }}
        >
          {evalCriteria.length > 0 && (
            <div className="w-96 h-full">
              <EvalCriteriaPanel
                evalCriteria={evalCriteria}
                patternState={patternState}
                onDismiss={() => setPanelVisible(false)}
                onUpdate={updateCriteria}
                onAdd={handleAddCriteria}
                onExportAll={handleExportAll}
              />
            </div>
          )}
        </div>
      </div>

      {/* ── Bottom Controls Bar (44px) ── */}
      <RunControls
        currentTranscriptLength={currentTranscript.length}
        onSaveRun={handleSaveRun}
        saving={saving}
      />
    </div>
  );
}

import { useState, useEffect } from "react";
import { useSession } from "./hooks/useSession";
import { generateCriteria } from "./api";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalCriteriaPanel from "./components/EvalCriteriaPanel";

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
  // "Discover Evals" shows for pattern_found, and for error-from-generation (criteriaError set)
  const showDiscoverButton =
    patternState === "pattern_found" ||
    (patternState === "error" && criteriaError !== null);

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Eval Discovery</h1>
          <p className="text-xs text-gray-500">Chat with Alex · CloudSync SDR</p>
        </div>
        <div className="flex items-center gap-3">
          {patternState === "checking" && (
            <span className="text-xs text-blue-600 animate-pulse font-medium">
              Checking for patterns…
            </span>
          )}
          {patternState === "generating" && (
            <span className="text-xs text-amber-600 animate-pulse font-medium">
              Generating eval…
            </span>
          )}
          {patternState === "error" && criteriaError !== null && (
            <span className="text-xs text-red-500">Eval generation failed — retry</span>
          )}
          {patternState === "error" && criteriaError === null && (
            <span className="text-xs text-red-500">
              Pattern check failed — retrying automatically
            </span>
          )}
          {patternState === "pattern_found" && evalCriteria.length === 0 && (
            <span className="text-xs text-green-600 font-medium">Pattern found</span>
          )}
          {patternState === "pattern_found" && evalCriteria.length > 0 && (
            <span className="text-xs text-green-600 font-medium">New pattern found</span>
          )}
          {patternState === "complete" && (
            <span className="text-xs text-blue-600 font-medium">Eval ready</span>
          )}
          {evalCriteria.length > 0 && (
            <button
              onClick={() => setPanelVisible(true)}
              className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded-full px-3 py-1 hover:bg-blue-100"
            >
              Eval Criteria ({evalCriteria.length})
            </button>
          )}
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: chat + controls */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <RunControls
            runCount={runCount}
            currentTranscriptLength={currentTranscript.length}
            patternState={patternState}
            showDiscoverButton={showDiscoverButton}
            onSaveRun={handleSaveRun}
            onDiscoverEvals={handleDiscoverEvals}
            saving={saving}
          />
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={isGenerating}
          />
        </div>

        {/* Right: eval criteria panel — kept mounted to preserve expandedIndex state,
             hidden via width collapse (spec requires state not be cleared on dismiss) */}
        {evalCriteria.length > 0 && (
          <div
            className="flex-shrink-0 overflow-hidden border-l transition-[width] duration-200"
            style={{ width: panelVisible ? 384 : 0 }}
          >
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
          </div>
        )}
      </div>
    </div>
  );
}

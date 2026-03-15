import { useState } from "react";
import { useSession } from "./hooks/useSession";
import { generateRubric } from "./api";
import ChatInterface from "./components/ChatInterface";
import RunControls from "./components/RunControls";
import EvalSuggestionPanel from "./components/EvalSuggestionPanel";

export default function App() {
  const {
    runCount,
    transcripts,
    currentTranscript,
    evalTriggered,
    evalDismissed,
    evalRubric,
    patternState,
    patternSummary,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    setPatternState,
  } = useSession();

  const [rubricError, setRubricError] = useState(null);
  const [saving, setSaving] = useState(false);

  async function handleSaveRun() {
    if (saving) return;
    setSaving(true);
    try {
      await saveRun({
        currentPatternState: patternState,
        currentTranscripts: transcripts,
        currentRunCount: runCount,
        currentCurrentTranscript: currentTranscript,
      });
    } finally {
      setSaving(false);
    }
  }

  async function handleDiscoverEvals() {
    if (!patternSummary) return;
    setPatternState("generating");
    setRubricError(null);
    markTriggered();
    try {
      const rubric = await generateRubric(patternSummary);
      saveRubric(rubric);
      setPatternState("complete");
    } catch (err) {
      setRubricError(err.message);
      setPatternState("error");
    }
  }

  function handleSaveRubric(edited) {
    saveRubric(edited);
    const blob = new Blob([JSON.stringify(edited, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "eval-rubric.json";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  const showEvalPanel = evalRubric && evalTriggered && !evalDismissed && patternState === "complete";
  const isChecking = patternState === "checking";
  const isGenerating = patternState === "generating";

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Eval Discovery</h1>
          <p className="text-xs text-gray-500">Chat with Alex · CloudSync SDR</p>
        </div>
        <div className="flex items-center gap-3">
          {isChecking && (
            <span className="text-xs text-blue-600 animate-pulse font-medium">
              Checking for patterns…
            </span>
          )}
          {isGenerating && (
            <span className="text-xs text-amber-600 animate-pulse font-medium">
              Generating eval…
            </span>
          )}
          {patternState === "error" && !isGenerating && (
            <span className="text-xs text-red-500">
              Pattern check failed — save another run to retry.
            </span>
          )}
          {rubricError && patternState === "error" && (
            <span className="text-xs text-red-500">{rubricError}</span>
          )}
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: chat */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <RunControls
            runCount={runCount}
            currentTranscriptLength={currentTranscript.length}
            patternState={patternState}
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

        {/* Right: eval panel */}
        {showEvalPanel && (
          <div className="w-96 flex-shrink-0 overflow-hidden">
            <EvalSuggestionPanel
              rubric={evalRubric}
              onDismiss={dismissEval}
              onSave={handleSaveRubric}
            />
          </div>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { useSession } from "./hooks/useSession";
import { analyzeTranscripts } from "./api";
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
    evalIntent,
    shouldTriggerAnalysis,
    threshold,
    addMessage,
    saveRun,
    markTriggered,
    dismissEval,
    saveRubric,
    saveIntent,
  } = useSession();

  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState(null);

  // Trigger analysis when threshold is crossed
  useEffect(() => {
    if (!shouldTriggerAnalysis || analyzing) return;

    async function runAnalysis() {
      setAnalyzing(true);
      setAnalyzeError(null);
      markTriggered();
      try {
        const allTranscripts = transcripts.map((t) =>
          t.map((m) => ({ role: m.role, content: m.content }))
        );
        const result = await analyzeTranscripts(allTranscripts, runCount);
        saveIntent(result.intent);
        saveRubric(result.rubric);
      } catch (err) {
        setAnalyzeError(err.message);
      } finally {
        setAnalyzing(false);
      }
    }

    runAnalysis();
  }, [shouldTriggerAnalysis, analyzing, transcripts, runCount, markTriggered, saveRubric, saveIntent]);

  const showEvalPanel =
    evalRubric && evalTriggered && !evalDismissed && !analyzing;

  function handleSaveRubric(edited) {
    saveRubric(edited);
    const blob = new Blob([JSON.stringify(edited, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "eval-rubric.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="flex flex-col h-screen bg-white">
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Eval Discovery</h1>
          <p className="text-xs text-gray-500">Chat with Alex · CloudSync SDR</p>
        </div>
        {analyzing && (
          <span className="text-xs text-amber-600 animate-pulse font-medium">
            Analyzing transcripts…
          </span>
        )}
        {analyzeError && (
          <span className="text-xs text-red-500">{analyzeError}</span>
        )}
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: chat */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <RunControls
            runCount={runCount}
            threshold={threshold}
            currentTranscriptLength={currentTranscript.length}
            onSaveRun={saveRun}
          />
          <ChatInterface
            currentTranscript={currentTranscript}
            onMessage={addMessage}
            disabled={false}
          />
        </div>

        {/* Right: eval panel (slides in when ready) */}
        {showEvalPanel && (
          <div className="w-96 flex-shrink-0 overflow-hidden">
            <EvalSuggestionPanel
              rubric={evalRubric}
              intent={evalIntent}
              onDismiss={dismissEval}
              onSave={handleSaveRubric}
            />
          </div>
        )}
      </div>
    </div>
  );
}

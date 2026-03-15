export default function RunControls({
  runCount,
  currentTranscriptLength,
  patternState,
  showDiscoverButton,
  onSaveRun,
  onDiscoverEvals,
  saving,
}) {
  const canSave = currentTranscriptLength >= 3;
  const discoverDisabled = patternState === "generating";

  return (
    <div className="flex items-center gap-4 px-4 py-3 bg-gray-50 border-b">
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500">Preview runs:</span>
        <span className="font-semibold text-gray-800">{runCount}</span>
      </div>

      <div className="flex-1" />

      {showDiscoverButton && (
        <button
          onClick={onDiscoverEvals}
          disabled={discoverDisabled}
          className="bg-amber-500 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-amber-600 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Discover Evals
        </button>
      )}

      <button
        onClick={onSaveRun}
        disabled={!canSave || saving}
        className="bg-gray-800 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        Save Run &amp; Start New
      </button>

      {!canSave && currentTranscriptLength > 0 && (
        <span className="text-xs text-gray-400">
          (need {3 - currentTranscriptLength} more turns)
        </span>
      )}
    </div>
  );
}

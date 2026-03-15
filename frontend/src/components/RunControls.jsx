export default function RunControls({ runCount, threshold, currentTranscriptLength, onSaveRun }) {
  const canSave = currentTranscriptLength >= 3;

  return (
    <div className="flex items-center gap-4 px-4 py-3 bg-gray-50 border-b">
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-500">Preview runs:</span>
        <span className="font-semibold text-gray-800">{runCount}</span>
        <span className="text-gray-400 text-sm">/ {threshold} to trigger eval</span>
      </div>

      <div className="flex-1" />

      <button
        onClick={onSaveRun}
        disabled={!canSave}
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

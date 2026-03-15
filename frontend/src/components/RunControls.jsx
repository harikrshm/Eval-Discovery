import { Button } from "./ui/button";

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
  // Defense-in-depth: showDiscoverButton is already false during "generating",
  // so this disabled state is normally unreachable. Guards against future prop changes.
  const discoverDisabled = patternState === "generating";

  return (
    <div className="h-11 flex items-center gap-3 px-6 border-t border-border bg-white flex-shrink-0">
      {/* Left: run count */}
      <span className="text-sm text-muted-foreground">
        Preview runs:{" "}
        <strong className="text-foreground font-semibold">{runCount}</strong>
      </span>

      <div className="flex-1" />

      {/* Right: action buttons */}
      {!canSave && currentTranscriptLength > 0 && (
        <span className="text-xs text-muted-foreground">
          (need {3 - currentTranscriptLength} more turns)
        </span>
      )}

      {showDiscoverButton && (
        <Button
          variant="outline"
          size="sm"
          onClick={onDiscoverEvals}
          disabled={discoverDisabled}
        >
          Discover Evals
        </Button>
      )}

      <Button
        size="sm"
        onClick={onSaveRun}
        disabled={!canSave || saving}
      >
        Save Run &amp; Start New
      </Button>
    </div>
  );
}

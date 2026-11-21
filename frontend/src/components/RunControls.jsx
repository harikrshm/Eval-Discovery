import { useId } from "react";
import { Button } from "./ui/button";

const MIN_MESSAGES = 3;

export default function RunControls({
  currentTranscriptLength,
  onSaveRun,
  saving,
  primary,
}) {
  const hintId = useId();
  const canSave = currentTranscriptLength >= MIN_MESSAGES;
  const showHint = currentTranscriptLength > 0 && !canSave;

  return (
    <div className="min-h-11 flex items-center justify-end gap-3 px-4 sm:px-6 py-1.5 border-t border-border bg-white flex-shrink-0">
      {showHint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {currentTranscriptLength} of {MIN_MESSAGES} messages. Keep chatting to end the run.
        </p>
      )}

      <Button
        size="sm"
        variant={primary ? "accent" : "outline"}
        onClick={onSaveRun}
        disabled={!canSave || saving}
        aria-describedby={showHint ? hintId : undefined}
      >
        End run
      </Button>
    </div>
  );
}

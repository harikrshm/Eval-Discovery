import { Button } from "./ui/button";

export default function RunControls({
  currentTranscriptLength,
  onSaveRun,
  saving,
}) {
  const canSave = currentTranscriptLength >= 3;

  return (
    <div className="h-11 flex items-center gap-3 px-6 border-t border-border bg-white flex-shrink-0">
      <div className="flex-1" />

      <Button
        size="sm"
        onClick={onSaveRun}
        disabled={!canSave || saving}
      >
        END
      </Button>
    </div>
  );
}

// After a run is saved from a snapshot of the draft, anything appended to the
// draft while the request was in flight has not been saved and must be kept.
export function remainingDraft(currentDraft, savedCount) {
  return currentDraft.slice(savedCount);
}

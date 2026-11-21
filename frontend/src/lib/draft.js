// After a run is saved from a snapshot of the draft, anything appended to the
// draft while the request was in flight has not been saved and must be kept.
export function remainingDraft(currentDraft, savedCount) {
  return currentDraft.slice(savedCount);
}

// The in-progress run is kept per agent in sessionStorage.
const DRAFT_PREFIX = "current_transcript:";

export const draftKey = (agentId) => `${DRAFT_PREFIX}${agentId}`;

// Forget every stored draft (used when the user deletes their data).
export function clearAllDrafts() {
  try {
    const keys = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      if (key && key.startsWith(DRAFT_PREFIX)) keys.push(key);
    }
    keys.forEach((key) => sessionStorage.removeItem(key));
  } catch {
    // Storage unavailable: nothing was persisted.
  }
}

// The pattern state machine is not persisted; on load it is derived from what the
// server knows: a stored pattern summary, or already-discovered criteria.
export function derivePatternState({ patternSummary, criteriaCount }) {
  if (patternSummary) return "pattern_found";
  if (criteriaCount > 0) return "complete";
  return "idle";
}

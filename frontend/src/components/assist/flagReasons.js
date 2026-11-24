// The fixed reasons a conversation can be flagged for, in words. One map for the flagged list's rows and
// the Quality tab's filter chips, so a chip always reads the same as the rows it selects. The order is
// the chips' order. Any other value is shown as "Other", never as the raw text.
export const FLAG_REASON_LABELS = {
  rated_down: "Thumbs-down",
  no_answer: "No answer",
  reasked: "Re-ask",
  citation_unverified: "Citation failure",
  auto_judge: "Auto-judge",
};

export function flagReasonLabel(reason) {
  return Object.hasOwn(FLAG_REASON_LABELS, reason) ? FLAG_REASON_LABELS[reason] : "Other";
}

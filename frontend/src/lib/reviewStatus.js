// What the change review says about itself, kept apart from the components so the visible text
// and the panel's screen reader announcement come from one place.

// The stored outcome status decides; the narrative prose is never read for it.
export function outcomeLine(candidate, outcome) {
  const status = outcome?.status ?? candidate.status;
  if (status === "promoted") return `v${candidate.number} is now live.`;
  return `v${candidate.number} was not promoted${outcome?.reason ? `: ${outcome.reason}` : "."}`;
}

export const GAVE_UP_TEXT = "Couldn't write the explanation. The results above are still correct.";

// `statusText` is the visible progress line; `announcement` is the message for the panel's single
// live region. The announcement carries no changing counts, so it is not repeated on every poll.
export function reviewStatus({ candidate, report, reportLoading, reportError, gaveUp }) {
  const n = candidate.number;
  const parent = candidate.parent_number;
  const outcome = report?.outcome ?? null;

  if (candidate.status === "approved_verifying") {
    const p = candidate.progress;
    return {
      statusText: p
        ? `Verifying v${n}: ${p.done} of ${p.total} tests done. Chat still uses v${parent}.`
        : `Verifying v${n}. Chat still uses v${parent}.`,
      announcement: `Verifying v${n}. Chat still uses v${parent}.`,
    };
  }
  if (reportError) return { statusText: "", announcement: "" };
  if (reportLoading && !report) {
    return { statusText: "Loading the suggested fix…", announcement: "Loading the suggested fix…" };
  }
  if (!report) return { statusText: "", announcement: "" };
  if (candidate.status === "proposed") {
    return { statusText: "", announcement: `Suggested fix v${n} is ready to review.` };
  }

  const line = outcomeLine(candidate, outcome);
  if (!outcome) return { statusText: "", announcement: line };
  if (outcome.narrative) return { statusText: "", announcement: `${line} The explanation is ready.` };
  if (outcome.explanation_unavailable) return { statusText: "", announcement: `${line} Explanation unavailable.` };
  if (gaveUp) return { statusText: "", announcement: `${line} Couldn't write the explanation.` };
  return { statusText: "Writing the explanation…", announcement: `${line} Writing the explanation…` };
}

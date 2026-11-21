import { ApiError } from "../api";

const VERDICT_LABELS = {
  pass: "Pass",
  fail: "Fail",
  not_applicable: "Not applicable",
};

const STATUS_LABELS = {
  passing: "Passing",
  failing: "Failing",
  untested: "Untested",
};

// A test with no current replay result has no verdict yet.
export function verdictLabel(verdict) {
  return VERDICT_LABELS[verdict] ?? "Not run";
}

export function statusLabel(status) {
  return STATUS_LABELS[status] ?? "Untested";
}

// A test is stale when it has no replay on the current prompt version (or its criterion changed).
export function staleMessage(count, versionNumber) {
  const noun = count === 1 ? "1 test hasn't" : `${count} tests haven't`;
  return `${noun} run on prompt v${versionNumber} yet.`;
}

// judge_check is null until the judge has been sanity-checked; `passed` is false when
// it could not tell the criterion's good example from its bad one.
export function lowConfidenceNote(judgeCheck) {
  if (!judgeCheck || judgeCheck.passed) return null;
  return "Low confidence: the judge did not correctly grade this criterion's own examples, so treat these verdicts with care.";
}

const UNREACHABLE = "Couldn't reach the server. Check your connection, then try again.";

// Text for a failure shown next to a job. Messages the server (ApiError) or the app wrote are
// kept; a raw browser network error ("Failed to fetch", a TypeError) is replaced with plain copy.
export function friendlyError(err) {
  if (err instanceof ApiError) return err.message || "Something went wrong.";
  if (err instanceof TypeError) return UNREACHABLE;
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

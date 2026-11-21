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

export function staleMessage(count) {
  const noun = count === 1 ? "1 test is" : `${count} tests are`;
  return `${noun} out of date. Run all tests.`;
}

// judge_check is null until the judge has been sanity-checked; `passed` is false when
// it could not tell the criterion's good example from its bad one.
export function lowConfidenceNote(judgeCheck) {
  if (!judgeCheck || judgeCheck.passed) return null;
  return "Low confidence: the judge did not correctly grade this criterion's own examples, so treat these verdicts with care.";
}

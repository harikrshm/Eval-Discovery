// How many of the suite's criteria are passing, counting only criteria that have at least one
// test (a criterion with no tests cannot be passing or failing yet). `withoutTests` lets the UI
// say how many criteria are not covered at all.
export function readiness(suite) {
  let passing = 0;
  let total = 0;
  let withoutTests = 0;
  for (const criterion of suite?.criteria ?? []) {
    if ((criterion.tests ?? []).length === 0) {
      withoutTests += 1;
      continue;
    }
    total += 1;
    if (criterion.status === "passing") passing += 1;
  }
  return { passing, total, withoutTests, allPassing: total > 0 && passing === total };
}

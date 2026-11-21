import { describe, expect, it } from "vitest";
import { readiness } from "./readiness.js";

const crit = (status, testCount) => ({
  criterion_id: `c-${Math.random()}`,
  status,
  tests: Array.from({ length: testCount }, (_, i) => ({ id: `t${i}` })),
});

describe("readiness", () => {
  it("is all passing when every criterion with tests passes", () => {
    expect(readiness({ criteria: [crit("passing", 2), crit("passing", 1)] })).toEqual({
      passing: 2,
      total: 2,
      withoutTests: 0,
      allPassing: true,
    });
  });

  it("is not all passing when one criterion fails or is untested", () => {
    expect(readiness({ criteria: [crit("passing", 1), crit("failing", 2)] })).toEqual({
      passing: 1,
      total: 2,
      withoutTests: 0,
      allPassing: false,
    });
    expect(readiness({ criteria: [crit("passing", 1), crit("untested", 1)] }).allPassing).toBe(false);
  });

  it("leaves criteria without tests out of the total and counts them separately", () => {
    expect(readiness({ criteria: [crit("passing", 1), crit("untested", 0), crit("untested", 0)] })).toEqual({
      passing: 1,
      total: 1,
      withoutTests: 2,
      allPassing: true,
    });
  });

  it("does not count a passing status on a criterion with no tests", () => {
    expect(readiness({ criteria: [crit("passing", 0)] })).toEqual({
      passing: 0,
      total: 0,
      withoutTests: 1,
      allPassing: false,
    });
  });

  it("is zeros for an empty, missing or null suite", () => {
    const zeros = { passing: 0, total: 0, withoutTests: 0, allPassing: false };
    expect(readiness({ criteria: [] })).toEqual(zeros);
    expect(readiness({})).toEqual(zeros);
    expect(readiness(null)).toEqual(zeros);
    expect(readiness(undefined)).toEqual(zeros);
  });
});

import { describe, expect, it } from "vitest";
import { MAX_REPLAY_VERSIONS, replayCandidates, replayRow } from "./replayData";
import { reportFor, versionRow } from "./testUtils";

describe("replayCandidates", () => {
  it("keeps only resolved versions that have a verification record, oldest first", () => {
    const versions = [
      versionRow(6, { origin: "suggested", status: "proposed" }),
      versionRow(5, { origin: "suggested", status: "approved_verifying", strength: "full" }),
      versionRow(4, { origin: "suggested", status: "failed_verification", strength: "weaker" }),
      versionRow(3, { origin: "suggested", status: "rejected" }),
      versionRow(2, { origin: "suggested", status: "promoted", strength: "full", is_current: true }),
      versionRow(1, { origin: "initial", status: "superseded" }),
    ];
    expect(replayCandidates(versions).map((v) => v.number)).toEqual([2, 4]);
  });

  it("is empty for anything that is not a list", () => {
    expect(replayCandidates(null)).toEqual([]);
    expect(replayCandidates(undefined)).toEqual([]);
    expect(replayCandidates({})).toEqual([]);
  });

  it("caps the list at the newest versions", () => {
    const versions = Array.from({ length: MAX_REPLAY_VERSIONS + 4 }, (_, i) =>
      versionRow(i + 1, { origin: "suggested", status: "superseded", strength: "full" })
    );
    const numbers = replayCandidates(versions).map((v) => v.number);
    expect(numbers).toHaveLength(MAX_REPLAY_VERSIONS);
    expect(numbers[0]).toBe(5);
    expect(numbers.at(-1)).toBe(MAX_REPLAY_VERSIONS + 4);
  });
});

describe("replayRow", () => {
  const version = versionRow(3, { origin: "suggested", status: "promoted", strength: "full" });

  it("counts passing tests before and after from the stored outcome table", () => {
    const report = reportFor(3, [["fail", "pass"], ["pass", "pass"], ["pass", "fail"], ["not_applicable", "not_applicable"]]);
    expect(replayRow(version, report)).toEqual({ number: 3, label: "v3", before: 2, after: 2, total: 4 });
  });

  it("leaves a side null when no test has a result on that side", () => {
    const report = reportFor(3, [["fail", null], ["pass", null]]);
    expect(replayRow(version, report)).toEqual({ number: 3, label: "v3", before: 1, after: null, total: 2 });
  });

  it("gives nothing when there is no outcome, no table or no result at all", () => {
    expect(replayRow(version, null)).toBeNull();
    expect(replayRow(version, { outcome: null })).toBeNull();
    expect(replayRow(version, { outcome: { table: [] } })).toBeNull();
    expect(replayRow(version, { outcome: { table: "x" } })).toBeNull();
    expect(replayRow(version, reportFor(3, [[null, null]]))).toBeNull();
  });

  it("ignores a verdict that is not a known string", () => {
    const report = reportFor(3, [[7, "pass"], ["pass", { v: 1 }]]);
    expect(replayRow(version, report)).toEqual({ number: 3, label: "v3", before: 1, after: 1, total: 2 });
  });
});

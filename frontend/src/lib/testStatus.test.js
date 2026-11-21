import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { friendlyError, lowConfidenceNote, staleMessage, statusLabel, verdictLabel } from "./testStatus";

describe("verdictLabel", () => {
  it("names each verdict and treats a missing one as not run", () => {
    expect(verdictLabel("pass")).toBe("Pass");
    expect(verdictLabel("fail")).toBe("Fail");
    expect(verdictLabel("not_applicable")).toBe("Not applicable");
    expect(verdictLabel(null)).toBe("Not run");
    expect(verdictLabel(undefined)).toBe("Not run");
  });
});

describe("statusLabel", () => {
  it("names each criterion status", () => {
    expect(statusLabel("passing")).toBe("Passing");
    expect(statusLabel("failing")).toBe("Failing");
    expect(statusLabel("untested")).toBe("Untested");
  });

  it("falls back to Untested for anything unknown", () => {
    expect(statusLabel(undefined)).toBe("Untested");
    expect(statusLabel("weird")).toBe("Untested");
  });
});

describe("staleMessage", () => {
  it("uses the singular for one test", () => {
    expect(staleMessage(1)).toBe("1 test is out of date. Run all tests.");
  });

  it("uses the plural otherwise", () => {
    expect(staleMessage(2)).toBe("2 tests are out of date. Run all tests.");
    expect(staleMessage(0)).toBe("0 tests are out of date. Run all tests.");
  });
});

describe("lowConfidenceNote", () => {
  it("is null when the judge check is absent or passed", () => {
    expect(lowConfidenceNote(null)).toBeNull();
    expect(lowConfidenceNote(undefined)).toBeNull();
    expect(lowConfidenceNote({ passed: true, good_verdict: "pass", bad_verdict: "fail" })).toBeNull();
  });

  it("is one sentence when the judge check failed", () => {
    const note = lowConfidenceNote({ passed: false, good_verdict: "fail", bad_verdict: "fail" });
    expect(typeof note).toBe("string");
    expect(note).toMatch(/low confidence/i);
    expect(note.match(/\./g)).toHaveLength(1);
  });
});

describe("friendlyError", () => {
  it("replaces a raw network failure with plain copy", () => {
    expect(friendlyError(new TypeError("Failed to fetch"))).toBe(
      "Couldn't reach the server. Check your connection, then try again.",
    );
    expect(friendlyError(new TypeError("NetworkError when attempting to fetch resource."))).toBe(
      "Couldn't reach the server. Check your connection, then try again.",
    );
  });

  it("keeps messages the server or the app wrote", () => {
    expect(friendlyError(new ApiError("Test run limit reached for this hour. Try again later.", 429))).toBe(
      "Test run limit reached for this hour. Try again later.",
    );
    expect(friendlyError(new Error("Timed out waiting for the job."))).toBe("Timed out waiting for the job.");
  });

  it("falls back for anything without a message", () => {
    expect(friendlyError(undefined)).toBe("Something went wrong.");
    expect(friendlyError(new Error(""))).toBe("Something went wrong.");
  });
});

import { describe, expect, it } from "vitest";
import { derivePatternState } from "./patternState.js";

describe("derivePatternState", () => {
  it("summary wins over criteria", () => {
    expect(derivePatternState({ patternSummary: "users ask about price", criteriaCount: 2 })).toBe(
      "pattern_found"
    );
  });

  it("criteria only -> complete", () => {
    expect(derivePatternState({ patternSummary: null, criteriaCount: 1 })).toBe("complete");
  });

  it("neither -> idle", () => {
    expect(derivePatternState({ patternSummary: null, criteriaCount: 0 })).toBe("idle");
  });

  it("empty-string summary counts as absent", () => {
    expect(derivePatternState({ patternSummary: "", criteriaCount: 0 })).toBe("idle");
    expect(derivePatternState({ patternSummary: "", criteriaCount: 3 })).toBe("complete");
  });
});

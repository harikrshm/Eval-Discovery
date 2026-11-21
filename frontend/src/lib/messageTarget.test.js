import { describe, expect, it } from "vitest";
import { targetsCurrentAgent } from "./messageTarget";

describe("targetsCurrentAgent", () => {
  it("accepts a message for the agent that is active", () => {
    expect(targetsCurrentAgent("a1", "a1")).toBe(true);
  });

  it("rejects a message addressed to an agent that is no longer active", () => {
    expect(targetsCurrentAgent("a2", "a1")).toBe(false);
    expect(targetsCurrentAgent(null, "a1")).toBe(false);
  });

  it("accepts a message with no explicit target (caller does not care)", () => {
    expect(targetsCurrentAgent("a2", undefined)).toBe(true);
  });
});

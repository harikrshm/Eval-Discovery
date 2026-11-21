import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { isAgentGone } from "./loadError";

describe("isAgentGone", () => {
  it("is true when the agent or workspace no longer exists (404, 401)", () => {
    expect(isAgentGone(new ApiError("Agent not found.", 404))).toBe(true);
    expect(isAgentGone(new ApiError("Invalid workspace token.", 401))).toBe(true);
  });

  it("is false for transient failures that keep the stored agent", () => {
    expect(isAgentGone(new ApiError("Bad gateway", 502))).toBe(false);
    expect(isAgentGone(new ApiError("Unavailable", 503))).toBe(false);
    expect(isAgentGone(new ApiError("Too many requests", 429))).toBe(false);
    expect(isAgentGone(new TypeError("Failed to fetch"))).toBe(false);
    expect(isAgentGone(null)).toBe(false);
  });
});

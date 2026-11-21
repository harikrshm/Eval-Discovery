import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import {
  MAX_DRAFT_MESSAGES,
  MAX_REQUEST_CHARS,
  MAX_USER_MESSAGE_CHARS,
  chatErrorMessage,
  draftLimitHint,
  draftLimitReason,
  runSaveErrorMessage,
} from "./chatLimits.js";

const turn = (i, content = "x") => ({ role: i % 2 === 0 ? "user" : "assistant", content });
const draftOf = (n, content) => Array.from({ length: n }, (_, i) => turn(i, content));

describe("limits mirror the server", () => {
  it("uses the server caps", () => {
    expect(MAX_USER_MESSAGE_CHARS).toBe(2000);
    expect(MAX_DRAFT_MESSAGES).toBe(40);
    expect(MAX_REQUEST_CHARS).toBe(120000);
  });
});

describe("draftLimitReason", () => {
  it("is null while another message still fits", () => {
    expect(draftLimitReason(draftOf(0), "hi")).toBeNull();
    expect(draftLimitReason(draftOf(39), "hi")).toBeNull();
  });

  it("reports the message cap once 40 messages are in the draft", () => {
    expect(draftLimitReason(draftOf(40), "hi")).toBe("messages");
  });

  it("reports the size cap when the next message would push past the total", () => {
    const big = draftOf(8, "a".repeat(15000)); // 120000 chars
    expect(draftLimitReason(big, "")).toBeNull();
    expect(draftLimitReason(big, "b")).toBe("size");
  });
});

describe("draftLimitHint", () => {
  it("explains how to continue", () => {
    expect(draftLimitHint("messages")).toMatch(/40/);
    expect(draftLimitHint("messages")).toMatch(/end the run|discard/i);
    expect(draftLimitHint("size")).toMatch(/too long|long/i);
    expect(draftLimitHint(null)).toBeNull();
  });
});

describe("chatErrorMessage", () => {
  it("shows the server message for a 4xx", () => {
    expect(chatErrorMessage(new ApiError("Too many requests. Wait a minute.", 429), "Alex")).toBe(
      "Too many requests. Wait a minute."
    );
    expect(chatErrorMessage(new ApiError("content too long", 422), "Alex")).toBe("content too long");
  });

  it("keeps the connection hint for network failures and 5xx", () => {
    const generic = "Alex didn't respond. Check your connection, then try again.";
    expect(chatErrorMessage(new TypeError("Failed to fetch"), "Alex")).toBe(generic);
    expect(chatErrorMessage(new ApiError("Bad gateway", 502), "Alex")).toBe(generic);
  });
});

describe("runSaveErrorMessage", () => {
  it("shows the server message for a 4xx and a generic one otherwise", () => {
    expect(runSaveErrorMessage(new ApiError("Run limit reached for this workspace.", 429))).toBe(
      "Run limit reached for this workspace."
    );
    expect(runSaveErrorMessage(new TypeError("Failed to fetch"))).toBe(
      "Couldn't save the run. Check your connection, then try again."
    );
  });
});

import { isAgentLimitError } from "./chatLimits.js";

describe("isAgentLimitError", () => {
  it("matches only the agent-limit 429", () => {
    expect(isAgentLimitError(new ApiError("Agent limit reached for this workspace.", 429))).toBe(true);
    expect(isAgentLimitError(new ApiError("Too many requests. Wait a minute.", 429))).toBe(false);
    expect(isAgentLimitError(new ApiError("Agent limit reached", 400))).toBe(false);
    expect(isAgentLimitError(new Error("Agent limit"))).toBe(false);
  });
});

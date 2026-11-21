import { describe, expect, it } from "vitest";
import { buildAgentPayload } from "./agentPayload";

const preset = {
  id: "support",
  name: "Support",
  system_prompt: "You are a support agent.",
  user_role_label: "Customer",
};

describe("buildAgentPayload", () => {
  it("sends only the preset id when a preset is unedited", () => {
    const form = { name: "Support", prompt: "You are a support agent.", roleLabel: "Customer" };
    expect(buildAgentPayload(preset, form)).toEqual({ preset_id: "support" });
  });

  it("sends the custom payload when the name was edited", () => {
    const form = { name: "Support 2", prompt: "You are a support agent.", roleLabel: "Customer" };
    expect(buildAgentPayload(preset, form)).toEqual({
      name: "Support 2",
      system_prompt: "You are a support agent.",
      user_role_label: "Customer",
    });
  });

  it("sends the custom payload when the prompt was edited", () => {
    const form = { name: "Support", prompt: "You are a terse support agent.", roleLabel: "Customer" };
    expect(buildAgentPayload(preset, form)).toEqual({
      name: "Support",
      system_prompt: "You are a terse support agent.",
      user_role_label: "Customer",
    });
  });

  it("sends the custom payload when the role label was edited or cleared", () => {
    const edited = { name: "Support", prompt: "You are a support agent.", roleLabel: "User" };
    expect(buildAgentPayload(preset, edited).user_role_label).toBe("User");
    const cleared = { name: "Support", prompt: "You are a support agent.", roleLabel: "" };
    expect(buildAgentPayload(preset, cleared)).toEqual({
      name: "Support",
      system_prompt: "You are a support agent.",
    });
  });

  it("builds a custom payload with no preset, trimming and omitting an empty label", () => {
    const form = { name: "  Bot ", prompt: " Be helpful. ", roleLabel: "   " };
    expect(buildAgentPayload(null, form)).toEqual({ name: "Bot", system_prompt: "Be helpful." });
  });

  it("includes a trimmed role label when given", () => {
    const form = { name: "Bot", prompt: "Be helpful.", roleLabel: " Guest " };
    expect(buildAgentPayload(null, form)).toEqual({
      name: "Bot",
      system_prompt: "Be helpful.",
      user_role_label: "Guest",
    });
  });
});

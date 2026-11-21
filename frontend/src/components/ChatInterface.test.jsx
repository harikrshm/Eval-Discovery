import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, sendMessage: vi.fn() };
});

import * as api from "../api";
import ChatInterface from "./ChatInterface";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const AGENT = { id: "a1", name: "Alex", user_role_label: "Prospect", starter_messages: [] };
const turn = (i) => ({ role: i % 2 === 0 ? "user" : "assistant", content: `m${i}` });

let container;
let root;

async function render(props) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(ChatInterface, {
        agent: AGENT,
        agentId: "a1",
        currentTranscript: [],
        onMessage: vi.fn(),
        onDiscard: vi.fn(),
        disabled: false,
        ...props,
      })
    );
  });
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

async function typeAndSend(text) {
  const input = container.querySelector("input");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    container.querySelector('[aria-label="Send message"]').click();
  });
}

beforeEach(() => vi.clearAllMocks());

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("ChatInterface", () => {
  it("caps the input at 2000 characters", async () => {
    await render({});
    expect(container.querySelector("input").maxLength).toBe(2000);
  });

  it("blocks sending and explains when the draft is at the message cap", async () => {
    await render({ currentTranscript: Array.from({ length: 40 }, (_, i) => turn(i)) });
    expect(container.querySelector("input").disabled).toBe(true);
    expect(container.querySelector('[role="status"]').textContent).toMatch(/40-message limit/);
    expect(container.querySelector('[aria-label="Send message"]').disabled).toBe(true);
  });

  it("shows no limit hint for a short draft", async () => {
    await render({ currentTranscript: [turn(0), turn(1)] });
    expect(container.textContent).not.toMatch(/message limit/);
  });

  it("discards the run after confirmation, without saving", async () => {
    const onDiscard = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await render({ currentTranscript: [turn(0), turn(1)], onDiscard });

    await act(async () => {
      buttonByText("Discard run / Start new run").click();
    });

    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("keeps the run when the discard confirmation is declined", async () => {
    const onDiscard = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await render({ currentTranscript: [turn(0)], onDiscard });

    await act(async () => {
      buttonByText("Discard run / Start new run").click();
    });

    expect(onDiscard).not.toHaveBeenCalled();
  });

  it("has no discard control for an empty draft", async () => {
    await render({});
    expect(buttonByText("Discard run")).toBeUndefined();
  });

  it("shows the server's message for a 4xx reply failure", async () => {
    api.sendMessage.mockRejectedValue(
      new api.ApiError("Too many requests. Wait a minute, then try again.", 429)
    );
    await render({});
    await typeAndSend("hello");

    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Too many requests. Wait a minute, then try again.");
    expect(alert.textContent).not.toMatch(/check your connection/i);
  });

  it("keeps the connection hint for a network failure", async () => {
    api.sendMessage.mockRejectedValue(new TypeError("Failed to fetch"));
    await render({});
    await typeAndSend("hello");

    expect(container.querySelector('[role="alert"]').textContent).toMatch(/check your connection/i);
  });
});

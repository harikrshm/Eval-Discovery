import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The console chunk is held back until the test lets it go, so the wait for it can be seen.
const gate = vi.hoisted(() => {
  const state = {};
  state.promise = new Promise((resolve) => {
    state.release = resolve;
  });
  return state;
});

vi.mock("./components/assist/AssistConsole", async () => {
  await gate.promise;
  const { createElement: h } = await import("react");
  return { default: () => h("h1", null, "Console stub") };
});

import App from "./App";
import { mount, settle, unmount } from "./components/assist/testUtils";

// The console is its own chunk (it carries the charting library), loaded only when connected.
describe("App when connected to the Assist console", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("ed_token", "demo");
    localStorage.setItem("ed_agent_id", "assist-1");
    localStorage.setItem("ed_assist_connected", "1");
    window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  });

  afterEach(unmount);

  it("shows a status line while the console chunk loads, then the console", async () => {
    const container = await mount(createElement(App));
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector('[role="status"]').textContent).toBe("Loading the console…");
    // The chat app is not rendered while connected.
    expect(container.textContent).not.toContain("Set up the agent");

    await act(async () => gate.release());
    await settle(10);
    expect(container.querySelector("h1").textContent).toBe("Console stub");
    expect(container.querySelector('[role="status"]')).toBeNull();
  });
});

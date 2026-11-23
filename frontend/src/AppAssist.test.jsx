import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    assistStatus: vi.fn(),
    assistMetrics: vi.fn(),
    assistFlagged: vi.fn(),
    assistInsights: vi.fn(),
    getAgentState: vi.fn(),
    getTests: vi.fn(),
    listPresets: vi.fn(),
  };
});

import * as api from "./api";
import App from "./App";
import { clearWorkspace, restoreAssistBackup } from "./lib/workspace";
import { STATUS, buttonByText, click, metricsFixture, mount, pressKey, settle, submit, type, unmount } from "./components/assist/testUtils";

const DEMO_TOKEN = "demo-secret-token-123";
const FIXED_ERROR = "That token does not belong to a Patent Assist workspace.";

let container;
let consoleSpies;

const entryButton = () => buttonByText(container, "Assist console");
const tokenInput = () => container.ownerDocument.querySelector('input[type="password"]');
const dialog = () => document.querySelector('[role="dialog"]');

async function openConnect() {
  await click(entryButton());
}

async function connectWith(token) {
  await openConnect();
  await type(tokenInput(), token);
  await submit(document.querySelector('[role="dialog"] form'));
}

function bodyText() {
  return document.body.textContent + document.body.innerHTML;
}

beforeEach(async () => {
  vi.resetAllMocks();
  // The module mirrors storage in memory; forget what an earlier test left there.
  restoreAssistBackup();
  clearWorkspace();
  localStorage.clear();
  sessionStorage.clear();
  document.body.innerHTML = "";
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  consoleSpies = ["log", "info", "warn", "error", "debug"].map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
  api.listPresets.mockResolvedValue([]);
  api.getAgentState.mockResolvedValue({
    agent: {
      id: "a1",
      name: "Alex",
      user_role_label: "Prospect",
      starter_messages: [],
      system_prompt: "You are Alex.",
      prompt_version_id: "pv1",
      prompt_version_number: 1,
    },
    run_count: 0,
    runs: [],
    criteria: [],
    pattern_summary: null,
  });
  api.getTests.mockResolvedValue(null);
  api.assistMetrics.mockResolvedValue(metricsFixture());
  api.assistFlagged.mockResolvedValue([]);
  localStorage.setItem("ed_token", "my-own-token");
  localStorage.setItem("ed_agent_id", "a1");
  container = await mount(createElement(App));
});

afterEach(async () => {
  await unmount();
  vi.restoreAllMocks();
});

describe("Assist console entry", () => {
  it("has an always-visible header button that opens a password connect box, Escape closes it", async () => {
    expect(dialog()).toBeNull();
    await openConnect();
    expect(dialog()).not.toBeNull();
    expect(tokenInput().type).toBe("password");
    expect(tokenInput().labels[0].textContent).toBe("Demo workspace token");
    expect(entryButton().getAttribute("aria-expanded")).toBe("true");

    await pressKey(document, "Escape");
    expect(dialog()).toBeNull();
    expect(document.activeElement).toBe(entryButton());
    // The normal app is unchanged.
    expect(container.textContent).toContain("Eval Discovery");
  });

  it("is also reachable from the first-run setup screen", async () => {
    await unmount();
    localStorage.clear();
    document.body.innerHTML = "";
    container = await mount(createElement(App));
    expect(container.textContent).toContain("Set up the agent you want to test");
    expect(entryButton()).toBeDefined();
  });
});

describe("connect", () => {
  it("swaps the workspace, keeps backups and points the app at the assistant agent", async () => {
    let tokenDuringCall = null;
    api.assistStatus.mockImplementation(async () => {
      tokenDuringCall ??= localStorage.getItem("ed_token");
      return STATUS;
    });
    await connectWith(DEMO_TOKEN);

    // The connect check, then the console's own load.
    expect(api.assistStatus).toHaveBeenCalledTimes(2);
    expect(tokenDuringCall).toBe(DEMO_TOKEN);
    expect(localStorage.getItem("ed_token")).toBe(DEMO_TOKEN);
    expect(localStorage.getItem("ed_token_backup")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id_backup")).toBe("a1");
    expect(localStorage.getItem("ed_agent_id")).toBe("assist-1");

    // The console replaces the app, and the connect box is gone.
    expect(document.body.textContent).toContain("Assist console");
    expect(document.body.textContent).toContain("demo dataset");
    expect(dialog()).toBeNull();
    expect(document.body.textContent).not.toContain("Alex");
  });

  it("never renders or logs the token, and clears the input while it is being checked", async () => {
    let release;
    api.assistStatus.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    await openConnect();
    await type(tokenInput(), DEMO_TOKEN);
    expect(tokenInput().value).toBe(DEMO_TOKEN);
    await submit(document.querySelector('[role="dialog"] form'));
    // Pending: the token is no longer in the input or anywhere in the page.
    expect(tokenInput().value).toBe("");
    expect(tokenInput().disabled).toBe(true);
    expect(bodyText()).not.toContain(DEMO_TOKEN);
    api.assistStatus.mockResolvedValue(STATUS);
    await act(async () => release(STATUS));
    await settle();

    expect(bodyText()).not.toContain(DEMO_TOKEN);
    for (const spy of consoleSpies) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain(DEMO_TOKEN);
    }
    expect(window.location.href).not.toContain(DEMO_TOKEN);
  });

  it("restores the previous workspace and shows a fixed error when the token is refused", async () => {
    api.assistStatus.mockImplementation(async () => {
      // request() forgets the workspace on a 401; the restore must still bring the old one back.
      clearWorkspace();
      throw new api.ApiError("Invalid workspace token", 401);
    });
    await connectWith(DEMO_TOKEN);

    expect(document.querySelector('[role="alert"]').textContent).toBe(FIXED_ERROR);
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id")).toBe("a1");
    expect(localStorage.getItem("ed_token_backup")).toBeNull();
    expect(localStorage.getItem("ed_agent_id_backup")).toBeNull();
    expect(tokenInput().value).toBe("");
    expect(bodyText()).not.toContain(DEMO_TOKEN);
    expect(container.textContent).toContain("Eval Discovery");
  });

  it("treats a 404 (no assistant agent) the same way", async () => {
    api.assistStatus.mockRejectedValue(new api.ApiError("No assistant agent in this workspace.", 404));
    await connectWith(DEMO_TOKEN);
    expect(document.querySelector('[role="alert"]').textContent).toBe(FIXED_ERROR);
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
  });

  it("restores the previous workspace on a network failure too, with its own message", async () => {
    api.assistStatus.mockRejectedValue(new TypeError("Failed to fetch"));
    await connectWith(DEMO_TOKEN);
    expect(document.querySelector('[role="alert"]').textContent).toBe("Couldn't reach the server. Try again.");
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id")).toBe("a1");
  });

  it("does not connect with an empty token", async () => {
    await openConnect();
    const connect = [...document.querySelectorAll('[role="dialog"] button')].find((b) => b.textContent === "Connect");
    expect(connect.disabled).toBe(true);
    await submit(document.querySelector('[role="dialog"] form'));
    expect(api.assistStatus).not.toHaveBeenCalled();
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
  });
});

describe("disconnect", () => {
  it("restores the token and agent id and returns to the normal app", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    await click(buttonByText(document.body, "Disconnect"));

    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id")).toBe("a1");
    expect(localStorage.getItem("ed_token_backup")).toBeNull();
    expect(localStorage.getItem("ed_agent_id_backup")).toBeNull();
    expect(localStorage.getItem("ed_assist_connected")).toBeNull();
    expect(document.body.textContent).toContain("Alex");
    expect(document.querySelector('[role="tablist"]')).toBeNull();
    expect(api.getAgentState).toHaveBeenLastCalledWith("a1");
  });

  it("clears the workspace when there was none before connecting", async () => {
    await unmount();
    localStorage.clear();
    document.body.innerHTML = "";
    container = await mount(createElement(App));
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    expect(localStorage.getItem("ed_token")).toBe(DEMO_TOKEN);

    await click(buttonByText(document.body, "Disconnect"));
    expect(localStorage.getItem("ed_token")).toBeNull();
    expect(localStorage.getItem("ed_agent_id")).toBeNull();
    expect(localStorage.getItem("ed_token_backup")).toBeNull();
    expect(document.body.textContent).toContain("Set up the agent you want to test");
  });

  it("stays connected across a reload and still disconnects to the original workspace", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    await unmount();
    document.body.innerHTML = "";
    container = await mount(createElement(App));
    expect(document.body.textContent).toContain("demo dataset");

    await click(buttonByText(document.body, "Disconnect"));
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id")).toBe("a1");
  });
});

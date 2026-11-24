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
    assistOverview: vi.fn(),
    getAgentState: vi.fn(),
    getTests: vi.fn(),
    listPresets: vi.fn(),
  };
});

import * as api from "./api";
import App from "./App";
import { clearWorkspace, commitAssistSwap, restoreAssistBackup } from "./lib/workspace";
import { STATUS, buttonByText, click, metricsFixture, mount, overviewFixture, pressKey, settle, submit, type, unmount } from "./components/assist/testUtils";

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
  api.assistOverview.mockResolvedValue(overviewFixture());
  // The console is a lazy chunk; load it up front so connecting renders it within a few ticks.
  await import("./components/assist/AssistConsole");
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
    let tokenArg = null;
    let storedDuringCheck = null;
    api.assistStatus.mockImplementation(async (token) => {
      if (tokenArg === null) {
        tokenArg = token ?? "none";
        storedDuringCheck = { token: localStorage.getItem("ed_token"), backup: localStorage.getItem("ed_token_backup") };
      }
      return STATUS;
    });
    await connectWith(DEMO_TOKEN);

    // The connect check gets the pasted token as an argument while storage still holds the user's
    // own workspace; then the console's own load runs against the swapped storage.
    expect(api.assistStatus).toHaveBeenCalledTimes(2);
    expect(tokenArg).toBe(DEMO_TOKEN);
    expect(storedDuringCheck).toEqual({ token: "my-own-token", backup: null });
    expect(api.assistStatus.mock.calls[1]).toEqual([]);
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

  it("leaves storage exactly as it was and shows a fixed error when the token is refused", async () => {
    const before = JSON.stringify({ ...localStorage });
    api.assistStatus.mockRejectedValue(new api.ApiError("Invalid workspace token", 401));
    await connectWith(DEMO_TOKEN);

    expect(document.querySelector('[role="alert"]').textContent).toBe(FIXED_ERROR);
    expect(JSON.stringify({ ...localStorage })).toBe(before);
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

  it("leaves the workspace alone on a network failure too, with its own message", async () => {
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

describe("reload during and after connecting", () => {
  async function reload() {
    await unmount();
    document.body.innerHTML = "";
    container = await mount(createElement(App));
  }

  it("a reload while the demo token is still being checked changes nothing: the user's workspace stays active", async () => {
    api.assistStatus.mockImplementation(() => new Promise(() => {})); // never answers
    await openConnect();
    await type(tokenInput(), DEMO_TOKEN);
    await submit(document.querySelector('[role="dialog"] form'));
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id")).toBe("a1");
    expect(localStorage.getItem("ed_token_backup")).toBeNull();
    expect(localStorage.getItem("ed_assist_connected")).toBeNull();

    await reload();
    expect(document.querySelector('[role="tablist"]')).toBeNull();
    expect(container.textContent).toContain("Alex");
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(JSON.stringify({ ...localStorage })).not.toContain(DEMO_TOKEN);
  });

  it("a reload after an interrupted swap (backup written, no marker) recovers the user's own workspace", async () => {
    localStorage.setItem("ed_token_backup", "my-own-token");
    localStorage.setItem("ed_agent_id_backup", "a1");
    localStorage.setItem("ed_token", DEMO_TOKEN);
    localStorage.setItem("ed_agent_id", "assist-1");

    await reload();
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_agent_id")).toBe("a1");
    expect(localStorage.getItem("ed_token_backup")).toBeNull();
    expect(localStorage.getItem("ed_assist_connected")).toBeNull();
    expect(document.querySelector('[role="tablist"]')).toBeNull();
    expect(api.getAgentState).toHaveBeenLastCalledWith("a1");
  });

  it("a reload with the marker set goes straight to the console", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    await reload();
    expect(document.querySelector('[role="tablist"]')).not.toBeNull();
    expect(localStorage.getItem("ed_token_backup")).toBe("my-own-token");
  });

  it("connecting a second time (from a stale tab) cannot replace the backup with the demo token", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    expect(() => commitAssistSwap("another-demo", "assist-2")).toThrow();
    expect(localStorage.getItem("ed_token_backup")).toBe("my-own-token");
    expect(localStorage.getItem("ed_token")).toBe(DEMO_TOKEN);
  });

  it("shows a fixed message when the swap is refused because another tab connected first", async () => {
    let release;
    api.assistStatus.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    await openConnect();
    await type(tokenInput(), DEMO_TOKEN);
    await submit(document.querySelector('[role="dialog"] form'));
    // Another tab finished connecting meanwhile.
    commitAssistSwap("tab-b-demo", "assist-1");
    await act(async () => release(STATUS));
    await settle();
    expect(document.querySelector('[role="alert"]').textContent).toBe(
      "Disconnect from the Assist console before connecting again."
    );
    expect(localStorage.getItem("ed_token_backup")).toBe("my-own-token");
    expect(localStorage.getItem("ed_token")).toBe("tab-b-demo");
  });
});

describe("destructive actions while connected", () => {
  it("does not render the setup screen or Delete my data, and deleteWorkspace refuses", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    expect(document.body.textContent).not.toContain("Delete my data");
    expect(document.body.textContent).not.toContain("Set up the agent you want to test");
    await expect(api.deleteWorkspace()).rejects.toThrow(
      "Disconnect from the Assist console before deleting workspace data."
    );
    expect(localStorage.getItem("ed_token")).toBe(DEMO_TOKEN);
  });
});

describe("a second tab", () => {
  function storageEvent(key, newValue) {
    window.dispatchEvent(new StorageEvent("storage", { key, newValue, storageArea: localStorage }));
  }

  it("moves a normal-app tab to the console when another tab connects", async () => {
    // Tab A connects: it writes shared storage; this tab (B) only sees the storage event.
    localStorage.setItem("ed_token_backup", "my-own-token");
    localStorage.setItem("ed_agent_id_backup", "a1");
    localStorage.setItem("ed_token", DEMO_TOKEN);
    localStorage.setItem("ed_agent_id", "assist-1");
    localStorage.setItem("ed_assist_connected", "1");
    api.assistStatus.mockResolvedValue(STATUS);
    expect(document.querySelector('[role="tablist"]')).toBeNull();

    await act(async () => storageEvent("ed_assist_connected", "1"));
    await settle();
    expect(document.querySelector('[role="tablist"]')).not.toBeNull();
    expect(document.body.textContent).not.toContain("Delete my data");
  });

  it("brings a console tab back to the normal app when another tab disconnects", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    expect(document.querySelector('[role="tablist"]')).not.toBeNull();

    // Tab A disconnects: storage is restored there.
    localStorage.setItem("ed_token", "my-own-token");
    localStorage.setItem("ed_agent_id", "a1");
    localStorage.removeItem("ed_token_backup");
    localStorage.removeItem("ed_agent_id_backup");
    localStorage.removeItem("ed_assist_connected");
    await act(async () => storageEvent("ed_assist_connected", null));
    await settle();

    expect(document.querySelector('[role="tablist"]')).toBeNull();
    expect(document.body.textContent).toContain("Alex");
    expect(api.getAgentState).toHaveBeenLastCalledWith("a1");
  });

  it("ignores storage events that do not change the connected state", async () => {
    await act(async () => storageEvent("ed_token", "something"));
    await settle();
    expect(document.body.textContent).toContain("Alex");
    await openConnect();
    await act(async () => storageEvent("ed_agent_id", "a2"));
    expect(dialog()).not.toBeNull();
  });
});

describe("focus", () => {
  it("moves focus to the console heading after connecting and to the entry button after disconnecting", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    expect(document.activeElement).toBe(document.querySelector("h1"));
    expect(document.activeElement.textContent.trim()).toBe("Assist console");

    await click(buttonByText(document.body, "Disconnect"));
    expect(document.activeElement).toBe(entryButton());
  });

  it("does not steal focus on a reload while connected", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    await unmount();
    document.body.innerHTML = "";
    container = await mount(createElement(App));
    expect(document.activeElement).toBe(document.body);
  });
});

describe("final fix wave (M10)", () => {
  it("Escape while the token is being checked closes the box and the result is ignored: nothing connects", async () => {
    let release;
    api.assistStatus.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    await openConnect();
    await type(tokenInput(), DEMO_TOKEN);
    await submit(document.querySelector('[role="dialog"] form'));
    const before = JSON.stringify({ ...localStorage });

    await pressKey(document, "Escape");
    expect(dialog()).toBeNull();
    await act(async () => release(STATUS)); // the server says yes, but the user has already left
    await settle();

    expect(JSON.stringify({ ...localStorage })).toBe(before);
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(localStorage.getItem("ed_assist_connected")).toBeNull();
    expect(document.querySelector('[role="tablist"]')).toBeNull();
    expect(container.textContent).toContain("Alex");
  });

  it("Cancel while checking behaves the same", async () => {
    let release;
    api.assistStatus.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));
    await openConnect();
    await type(tokenInput(), DEMO_TOKEN);
    await submit(document.querySelector('[role="dialog"] form'));
    await click(buttonByText(document.body, "Cancel"));
    await act(async () => release(STATUS));
    await settle();
    expect(localStorage.getItem("ed_token")).toBe("my-own-token");
    expect(document.querySelector('[role="tablist"]')).toBeNull();
  });

  it("a storage event from sessionStorage never moves the tab", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    localStorage.setItem("ed_token_backup", "my-own-token");
    localStorage.setItem("ed_agent_id_backup", "a1");
    localStorage.setItem("ed_token", DEMO_TOKEN);
    localStorage.setItem("ed_agent_id", "assist-1");
    localStorage.setItem("ed_assist_connected", "1");
    await act(async () =>
      window.dispatchEvent(new StorageEvent("storage", { key: "ed_assist_connected", newValue: "1", storageArea: sessionStorage }))
    );
    await settle();
    expect(document.querySelector('[role="tablist"]')).toBeNull();
    expect(container.textContent).toContain("Alex");
    // the same event from localStorage does
    await act(async () =>
      window.dispatchEvent(new StorageEvent("storage", { key: "ed_assist_connected", newValue: "1", storageArea: localStorage }))
    );
    await settle();
    expect(document.querySelector('[role="tablist"]')).not.toBeNull();
  });

  it("a pending focus request does not fire later when the app came back without the entry button", async () => {
    api.assistStatus.mockResolvedValue(STATUS);
    await connectWith(DEMO_TOKEN);
    // The restored agent fails to load (a transient error): no entry button on screen.
    api.getAgentState.mockRejectedValueOnce(new Error("boom"));
    await click(buttonByText(document.body, "Disconnect"));
    expect(entryButton()).toBeUndefined();
    expect(document.body.textContent).toContain("Retry");

    // Much later the retry works and the header (with the entry button) appears: focus must stay put.
    await click(buttonByText(document.body, "Retry"));
    expect(entryButton()).toBeDefined();
    expect(document.activeElement).not.toBe(entryButton());
  });
});

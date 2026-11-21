import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getAgentState: vi.fn(),
    getTests: vi.fn(),
    getJob: vi.fn(),
    getVersion: vi.fn(),
    getReport: vi.fn(),
    approveVersion: vi.fn(),
    rejectVersion: vi.fn(),
    addCriterion: vi.fn(),
    suggestFix: vi.fn(),
    generateHeldout: vi.fn(),
  };
});

import * as api from "./api";
import App from "./App";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

let container;
let root;

const CRITERION = {
  id: "c1",
  name: "Stays on topic",
  why_discovered: "Seen in two runs.",
  definition: "d",
  good_example: "g",
  bad_example: "b",
};

const AGENT_V1 = {
  id: "a1",
  name: "Alex",
  user_role_label: "Prospect",
  starter_messages: [],
  system_prompt: "You are Alex.",
  prompt_version_id: "pv1",
  prompt_version_number: 1,
};

const AGENT_V2 = {
  ...AGENT_V1,
  system_prompt: "You are Alex, careful.",
  prompt_version_id: "pv2",
  prompt_version_number: 2,
};

function agentState(agent) {
  return { agent, run_count: 0, runs: [], criteria: [CRITERION], pattern_summary: null };
}

const CANDIDATE = {
  version_id: "v3",
  number: 3,
  status: "proposed",
  origin: "suggested",
  strength: "full",
  created_at: "2026-11-22T10:00:00",
  progress: null,
  parent_number: 2,
};

function suiteFor({ versionId = "pv2", versionNumber = 2, candidate = null } = {}) {
  return {
    prompt_version_id: versionId,
    prompt_version_number: versionNumber,
    stale_count: 0,
    candidate,
    criteria: [
      {
        criterion_id: "c1",
        status: "failing",
        judge_check: null,
        active_job_id: null,
        heldout_job_id: null,
        can_generate_heldout: false,
        tests: [
          {
            id: "t1",
            role: "fix",
            origin: "run",
            baseline: null,
            current: null,
            active_job_id: null,
            suggest_job_id: null,
            can_suggest_fix: false,
          },
        ],
      },
    ],
  };
}

function reportFor(status, outcome = null) {
  return {
    version_id: "v3",
    number: 3,
    status,
    strength: "full",
    why: {
      criterion_name: "Stays on topic",
      failing_quote: "q",
      change_summary: "s",
      touched_section: "t",
      expected_effect: "e",
      risk: "r",
    },
    outcome,
  };
}

async function settle(times = 4) {
  for (let i = 0; i < times; i += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

async function renderApp() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(App));
  });
  await settle();
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

// The criteria cards have textareas too; the prompt editor's is the one inside its section.
const promptTextarea = () => container.querySelector('section[aria-label="Agent prompt"] textarea');

const MESSAGE = "Couldn't load the latest prompt. Try again.";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = "";
  localStorage.clear();
  localStorage.setItem("ed_token", "tok");
  localStorage.setItem("ed_agent_id", "a1");
  sessionStorage.clear();
  vi.resetAllMocks();
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  api.getVersion.mockResolvedValue({ id: "v3", number: 3, text: "new", parent_text: "old" });
  api.getReport.mockResolvedValue(reportFor("proposed"));
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("App prompt version sync", () => {
  it("reloads the agent when the suite is on a different prompt version, and the editor shows it", async () => {
    api.getAgentState.mockResolvedValueOnce(agentState(AGENT_V1)).mockResolvedValue(agentState(AGENT_V2));
    api.getTests.mockResolvedValue(suiteFor());
    await renderApp();

    expect(api.getAgentState).toHaveBeenCalledTimes(2);
    const editor = container.querySelector('section[aria-label="Agent prompt"]');
    expect(editor.textContent).toContain("Chat uses v2");

    // Versions agree now, so nothing keeps reloading.
    await settle();
    expect(api.getAgentState).toHaveBeenCalledTimes(2);
  });

  it("does not reload when the agent and the suite agree", async () => {
    api.getAgentState.mockResolvedValue(agentState(AGENT_V2));
    api.getTests.mockResolvedValue(suiteFor());
    await renderApp();
    expect(api.getAgentState).toHaveBeenCalledTimes(1);
  });

  it("tries once and stops after a failed reload, then tries again when the suite's version changes", async () => {
    api.getAgentState.mockResolvedValueOnce(agentState(AGENT_V1)).mockRejectedValue(new TypeError("Failed to fetch"));
    api.getTests.mockResolvedValue(suiteFor());
    await renderApp();
    await settle();
    expect(api.getAgentState).toHaveBeenCalledTimes(2);

    // Adding a criterion reloads the suite; this time it reports another version, so one more try is made.
    api.addCriterion.mockResolvedValue({ ...CRITERION, id: "c2", name: "New criterion" });
    api.getTests.mockResolvedValue(suiteFor({ versionId: "pv4", versionNumber: 4 }));
    await act(async () => {
      buttonByText("Add criterion").click();
    });
    await settle();
    expect(api.getAgentState).toHaveBeenCalledTimes(3);
    await settle();
    expect(api.getAgentState).toHaveBeenCalledTimes(3);
  });
});

describe("App editing the prompt", () => {
  it("refuses to open the editor on a stale prompt when the reload fails", async () => {
    api.getAgentState.mockResolvedValueOnce(agentState(AGENT_V2)).mockRejectedValue(new TypeError("Failed to fetch"));
    api.getTests.mockResolvedValue(suiteFor());
    await renderApp();
    expect(api.getAgentState).toHaveBeenCalledTimes(1);

    await act(async () => {
      buttonByText("Edit prompt").click();
    });
    await settle();
    expect(api.getAgentState).toHaveBeenCalledTimes(2);
    expect(promptTextarea()).toBeNull();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => a.textContent === MESSAGE);
    expect(alert).toBeDefined();
  });

  it("opens the editor on the latest prompt after a successful reload", async () => {
    api.getAgentState.mockResolvedValueOnce(agentState(AGENT_V1)).mockResolvedValue(agentState(AGENT_V2));
    api.getTests.mockResolvedValue(suiteFor({ versionId: "pv1", versionNumber: 1 }));
    await renderApp();
    expect(container.querySelector('section[aria-label="Agent prompt"]').textContent).toContain("Chat uses v1");

    api.getAgentState.mockResolvedValue(agentState(AGENT_V2));
    await act(async () => {
      buttonByText("Edit prompt").click();
    });
    await settle();
    expect(promptTextarea().value).toBe("You are Alex, careful.");
  });
});

describe("App change review", () => {
  it("approves through the wired handler and moves focus to the verifying state", async () => {
    api.getAgentState.mockResolvedValue(agentState(AGENT_V2));
    api.getTests.mockResolvedValueOnce(suiteFor({ candidate: CANDIDATE }));
    api.approveVersion.mockResolvedValue({ jobs: [] });
    await renderApp();
    expect(buttonByText("Approve and verify")).toBeDefined();

    api.getTests.mockResolvedValue(
      suiteFor({ candidate: { ...CANDIDATE, status: "approved_verifying", progress: { done: 0, total: 1 } } })
    );
    api.getReport.mockResolvedValue(reportFor("approved_verifying"));
    await act(async () => {
      buttonByText("Approve and verify").click();
    });
    await settle();

    expect(api.approveVersion).toHaveBeenCalledWith("v3");
    expect(document.activeElement.tagName).toBe("H3");
    expect(document.activeElement.textContent).toBe("What happened with v3");
    // The editor now names the candidate and refuses edits until verification ends.
    const editor = container.querySelector('section[aria-label="Agent prompt"]');
    expect(editor.textContent).toContain("Chat uses v2 · v3 verifying");
    expect(buttonByText("Edit prompt").disabled).toBe(true);
  });

  it("leaves focus alone when the approval fails", async () => {
    api.getAgentState.mockResolvedValue(agentState(AGENT_V2));
    api.getTests.mockResolvedValue(suiteFor({ candidate: CANDIDATE }));
    api.approveVersion.mockRejectedValue(new Error("A fix is already proposed or being verified."));
    await renderApp();

    const approve = buttonByText("Approve and verify");
    approve.focus();
    await act(async () => {
      approve.click();
    });
    await settle();
    expect(api.approveVersion).toHaveBeenCalledWith("v3");
    expect(document.activeElement.tagName).not.toBe("H3");
    expect(container.textContent).toContain("A fix is already proposed or being verified.");
  });
});

describe("App readiness chip", () => {
  it("shows the same readiness in the app header and the panel, announced by one live region", async () => {
    api.getAgentState.mockResolvedValue(agentState(AGENT_V2));
    api.getTests.mockResolvedValue(suiteFor());
    await renderApp();

    const chips = [...container.querySelectorAll('[data-testid="readiness-chip"]')];
    expect(chips).toHaveLength(2);
    expect(chips.map((c) => c.textContent)).toEqual(["0 of 1 criteria passing", "0 of 1 criteria passing"]);
    expect(chips.filter((c) => c.getAttribute("role") === "status")).toHaveLength(1);
    expect(container.querySelector("header").contains(chips[0])).toBe(true);
  });
});

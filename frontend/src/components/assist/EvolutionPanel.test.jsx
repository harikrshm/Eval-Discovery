import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    getAgentState: vi.fn(),
    getTests: vi.fn(),
    getJob: vi.fn(),
    getVersions: vi.fn(),
    checkPattern: vi.fn(),
    discoverCriterion: vi.fn(),
    exportSuite: vi.fn(),
  };
});

import * as api from "../../api";
import EvolutionPanel from "./EvolutionPanel";
import { buttonByText, click, mount, settle, unmount } from "./testUtils";

let container;

const AGENT = {
  id: "assist-1",
  name: "Patent Assist",
  user_role_label: "Searcher",
  system_prompt: "You help people read patents.",
  prompt_version_id: "pv1",
  prompt_version_number: 1,
};

const CRITERION = {
  id: "c1",
  name: "Cites the claim it explains",
  why_discovered: "Seen in flagged conversations.",
  definition: "d",
  good_example: "g",
  bad_example: "b",
};

const SUITE = { prompt_version_id: "pv1", prompt_version_number: 1, stale_count: 0, candidate: null, criteria: [] };

function state({ criteria = [], pattern_summary = null } = {}) {
  return { agent: AGENT, run_count: 3, runs: [], criteria, pattern_summary };
}

async function renderPanel() {
  container = await mount(createElement(EvolutionPanel, { agentId: "assist-1" }));
}

const checkButton = () => buttonByText(container, "Check flagged conversations for a pattern");
const discoverButton = () => buttonByText(container, "Discover evals");

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  sessionStorage.clear();
  vi.resetAllMocks();
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  api.getAgentState.mockResolvedValue(state());
  api.getTests.mockResolvedValue(SUITE);
});

afterEach(async () => {
  await unmount();
  vi.useRealTimers();
});

describe("Evolution panel", () => {
  it("loads the assistant agent and offers the check button; discover is hidden until a pattern exists", async () => {
    await renderPanel();
    expect(api.getAgentState).toHaveBeenCalledWith("assist-1");
    expect(api.getTests).toHaveBeenCalledWith("assist-1");
    expect(checkButton()).toBeDefined();
    expect(discoverButton()).toBeUndefined();
    expect(container.textContent).toContain("No eval criteria yet.");
  });

  it("renders the existing criteria panel (tests, prompt editor, history) for the agent, with nothing to close", async () => {
    api.getAgentState.mockResolvedValue(state({ criteria: [CRITERION] }));
    await renderPanel();
    const panel = container.querySelector("h2");
    expect(panel.textContent).toBe("Eval criteria");
    expect(container.textContent).toContain("Cites the claim it explains");
    expect(container.querySelector('section[aria-label="Agent prompt"]')).not.toBeNull();
    expect(container.querySelector('button[aria-label="Close eval criteria panel"]')).toBeNull();
  });

  it("checks for a pattern on the assistant agent and shows the summary, then offers Discover evals", async () => {
    api.checkPattern.mockResolvedValue({ found: true, pattern_summary: "Answers skip the claim number." });
    await renderPanel();
    await click(checkButton());
    expect(api.checkPattern).toHaveBeenCalledWith("assist-1");
    expect(container.textContent).toContain("Pattern found: Answers skip the claim number.");
    expect(discoverButton()).toBeDefined();
  });

  it("says when no pattern was found and offers no discovery", async () => {
    api.checkPattern.mockResolvedValue({ found: false, pattern_summary: null });
    await renderPanel();
    await click(checkButton());
    expect(container.textContent).toContain("No pattern found in the flagged conversations.");
    expect(discoverButton()).toBeUndefined();
  });

  it("shows the server's text when the check fails, and a fixed text for other failures", async () => {
    api.checkPattern.mockRejectedValueOnce(new api.ApiError("No new conversations to check.", 400));
    await renderPanel();
    await click(checkButton());
    expect(container.querySelector('[role="alert"]').textContent).toBe("No new conversations to check.");

    api.checkPattern.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await click(checkButton());
    expect(container.querySelector('[role="alert"]').textContent).toBe("The pattern check failed. Please try again.");
    expect(discoverButton()).toBeUndefined();
  });

  it("offers Discover evals when the agent already has a pattern summary, and refreshes the tests after discovery", async () => {
    api.getAgentState.mockResolvedValue(state({ pattern_summary: "Earlier summary." }));
    api.discoverCriterion.mockResolvedValue({ ...CRITERION, job_id: "j1" });
    await renderPanel();
    expect(container.textContent).toContain("Pattern found: Earlier summary.");
    const testsBefore = api.getTests.mock.calls.length;

    await click(discoverButton());
    expect(api.discoverCriterion).toHaveBeenCalledWith("assist-1");
    expect(api.getTests.mock.calls.length).toBeGreaterThan(testsBefore);
    // The new criterion shows in the existing panel and the button is gone.
    expect(container.textContent).toContain("Cites the claim it explains");
    expect(discoverButton()).toBeUndefined();
  });

  it("discovers right after a successful check, and keeps the button with the error when it fails", async () => {
    api.checkPattern.mockResolvedValue({ found: true, pattern_summary: "S" });
    api.discoverCriterion.mockRejectedValueOnce(new api.ApiError("Eval generation failed.", 502));
    await renderPanel();
    await click(checkButton());
    await click(discoverButton());
    expect(api.discoverCriterion).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="alert"]').textContent).toBe("Eval generation failed.");
    expect(discoverButton()).toBeDefined();

    api.discoverCriterion.mockResolvedValueOnce({ ...CRITERION, job_id: "j1" });
    await click(discoverButton());
    expect(container.textContent).toContain("Cites the claim it explains");
    expect(discoverButton()).toBeUndefined();
  });

  it("disables the check button while a check is running", async () => {
    let resolve;
    api.checkPattern.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderPanel();
    await click(checkButton());
    expect(checkButton().disabled).toBe(true);
    await act(async () => resolve({ found: false, pattern_summary: null }));
    await settle();
    expect(checkButton().disabled).toBe(false);
  });

  it("shows a load error with a retry", async () => {
    api.getAgentState.mockRejectedValueOnce(new api.ApiError("Boom", 500)).mockResolvedValue(state());
    await renderPanel();
    expect(container.querySelector('[role="alert"]').textContent).toContain("Boom");
    await click(buttonByText(container, "Retry"));
    expect(checkButton()).toBeDefined();
  });
});

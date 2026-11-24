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
    getReport: vi.fn(),
    assistPipeline: vi.fn(),
    // The Evolution tab reads none of these; they are mocked so a test can prove it.
    assistOverview: vi.fn(),
    assistTrends: vi.fn(),
    assistMetrics: vi.fn(),
    assistQuality: vi.fn(),
    assistFlagged: vi.fn(),
  };
});

import * as api from "../../api";
import EvolutionPanel from "./EvolutionPanel";
import {
  FILTERS,
  buttonByText,
  click,
  mount,
  pipelineFixture,
  rerender,
  reportFor,
  settle,
  unmount,
  versionRow,
} from "./testUtils";

// jsdom has no layout, so Recharts' ResponsiveContainer would measure 0 x 0 and draw no bars.
class SizedObserver {
  constructor(callback) {
    this.callback = callback;
  }
  observe(target) {
    const box = { width: 640, height: 320 };
    this.callback([{ target, contentRect: box, borderBoxSize: [{ inlineSize: 640, blockSize: 320 }] }], this);
  }
  unobserve() {}
  disconnect() {}
}

// Real Recharts charts render in some tests; slow under a loaded full run.
vi.setConfig({ testTimeout: 30000 });

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

async function renderPanel(props = {}) {
  container = await mount(createElement(EvolutionPanel, { agentId: "assist-1", ...props }));
}

// Versions the live agent has: v3 promoted and current, v2 failed verification, v1 the first prompt.
const VERSIONS = [
  versionRow(3, { origin: "suggested", status: "promoted", strength: "full", is_current: true, created_at: "2026-11-22T09:30:00" }),
  versionRow(2, { origin: "suggested", status: "failed_verification", strength: "weaker", created_at: "2026-11-21T08:00:00" }),
  versionRow(1, { created_at: "2026-11-20T10:00:00" }),
];

const REPORTS = {
  pv3: reportFor(3, [["fail", "pass"], ["pass", "pass"]]),
  pv2: reportFor(2, [["fail", "fail"], ["pass", "fail"]], { status: "failed_verification" }),
};

const sectionOf = (title) =>
  [...container.querySelectorAll("section")].find((s) => s.querySelector("h3")?.textContent === title);

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
  api.assistPipeline.mockResolvedValue(pipelineFixture());
  api.getVersions.mockResolvedValue(VERSIONS);
  api.getReport.mockImplementation(async (id) => REPORTS[id]);
  globalThis.ResizeObserver = SizedObserver;
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

  it("keeps the actions in the same order: the pattern check runs before discovery", async () => {
    api.checkPattern.mockResolvedValue({ found: true, pattern_summary: "S" });
    api.discoverCriterion.mockResolvedValue({ ...CRITERION, job_id: "j1" });
    await renderPanel();
    await click(checkButton());
    await click(discoverButton());
    expect(api.checkPattern.mock.invocationCallOrder[0]).toBeLessThan(api.discoverCriterion.mock.invocationCallOrder[0]);
    expect(api.checkPattern).toHaveBeenCalledTimes(1);
    expect(api.discoverCriterion).toHaveBeenCalledTimes(1);
  });
});

describe("Evolution tab: the loop as a pipeline", () => {
  it("shows the pipeline steps with the current one marked, in a section alerts can link to", async () => {
    await renderPanel({ filters: FILTERS, reloadKey: 0 });
    const section = container.querySelector("#evolution-loop");
    expect(section).not.toBeNull();
    expect(section.querySelectorAll("ol > li")).toHaveLength(8);
    const current = section.querySelector('[aria-current="step"]');
    expect(current.textContent).toContain("Criterion chosen");
    expect(current.textContent).toContain("Current");
    expect(section.textContent).toContain("The loop repeats: a rejected fix sends you back to the fix step.");
    // The pipeline belongs to the agent: it is read once, without the console filters.
    expect(api.assistPipeline).toHaveBeenCalledTimes(1);
    const sent = api.assistPipeline.mock.calls[0][0];
    expect(sent.range).toBeUndefined();
    expect(sent.source).toBeUndefined();
  });

  it("shows the stepper above the existing actions", async () => {
    await renderPanel();
    const stepper = container.querySelector("#evolution-loop");
    expect(stepper.compareDocumentPosition(checkButton()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says the tab ignores the range, source and version filters", async () => {
    await renderPanel({ filters: FILTERS });
    expect(container.textContent).toContain("Applies to the whole assistant, not the selected range.");
  });

  it("does not refetch when only the filters change, but refetches on Refresh", async () => {
    await renderPanel({ filters: FILTERS, reloadKey: 0 });
    expect(api.assistPipeline).toHaveBeenCalledTimes(1);
    const versionCalls = api.getVersions.mock.calls.length;

    await rerender(createElement(EvolutionPanel, { agentId: "assist-1", filters: { ...FILTERS, range: "7d", version: 2 }, reloadKey: 0 }));
    expect(api.assistPipeline).toHaveBeenCalledTimes(1);
    expect(api.getVersions.mock.calls.length).toBe(versionCalls);

    await rerender(createElement(EvolutionPanel, { agentId: "assist-1", filters: FILTERS, reloadKey: 1 }));
    expect(api.assistPipeline).toHaveBeenCalledTimes(2);
    expect(api.getVersions.mock.calls.length).toBeGreaterThan(versionCalls);
  });

  it("shows Updating while a Refresh is in flight and keeps the old steps visible", async () => {
    await renderPanel({ filters: FILTERS, reloadKey: 0 });
    let resolve;
    api.assistPipeline.mockReturnValue(new Promise((r) => (resolve = r)));
    await rerender(createElement(EvolutionPanel, { agentId: "assist-1", filters: FILTERS, reloadKey: 1 }));
    expect(container.textContent).toContain("Updating…");
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(container.querySelectorAll("#evolution-loop ol > li")).toHaveLength(8);
    await act(async () => resolve(pipelineFixture({ cycle_note: "New note." })));
    await settle();
    expect(container.textContent).not.toContain("Updating…");
    expect(container.querySelector("#evolution-loop").textContent).toContain("New note.");
  });

  it("shows a loading line, then the steps, and the actions are there the whole time", async () => {
    let resolve;
    api.assistPipeline.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderPanel();
    expect(container.querySelector("#evolution-loop").textContent).toContain("Loading…");
    expect(checkButton()).toBeDefined();
    await act(async () => resolve(pipelineFixture()));
    await settle();
    expect(container.querySelectorAll("#evolution-loop ol > li")).toHaveLength(8);
  });

  it("shows a pipeline error with Retry and leaves the existing actions working", async () => {
    api.assistPipeline.mockRejectedValueOnce(new Error("Pipeline down"));
    api.checkPattern.mockResolvedValue({ found: false, pattern_summary: null });
    await renderPanel();
    const alert = container.querySelector("#evolution-loop [role='alert']");
    expect(alert.textContent).toContain("Couldn't load the improvement loop: Pipeline down");
    expect(checkButton()).toBeDefined();
    await click(checkButton());
    expect(api.checkPattern).toHaveBeenCalledWith("assist-1");

    await click(buttonByText(alert, "Retry"));
    expect(container.querySelector("#evolution-loop [role='alert']")).toBeNull();
    expect(container.querySelectorAll("#evolution-loop ol > li")).toHaveLength(8);
  });

  it("shows the loop even when the agent itself cannot load", async () => {
    api.getAgentState.mockRejectedValue(new api.ApiError("Boom", 500));
    await renderPanel();
    expect(container.querySelectorAll("#evolution-loop ol > li")).toHaveLength(8);
    expect(container.textContent).toContain("Couldn't load the assistant agent: Boom");
  });

  it("reads the pipeline again when a pattern check finds a pattern, so it never trails the buttons", async () => {
    api.checkPattern.mockResolvedValue({ found: true, pattern_summary: "Answers skip the claim number." });
    await renderPanel();
    expect(api.assistPipeline).toHaveBeenCalledTimes(1);
    api.assistPipeline.mockResolvedValue(pipelineFixture({ cycle_note: "After the check." }));
    await click(checkButton());
    expect(api.assistPipeline).toHaveBeenCalledTimes(2);
    expect(container.querySelector("#evolution-loop").textContent).toContain("After the check.");
  });

  it("does not dim the tab or say Updating while the loop-triggered refetch is in flight", async () => {
    api.checkPattern.mockResolvedValue({ found: true, pattern_summary: "S" });
    await renderPanel();
    let resolvePipeline;
    let resolveVersions;
    api.assistPipeline.mockReturnValue(new Promise((r) => (resolvePipeline = r)));
    api.getVersions.mockReturnValue(new Promise((r) => (resolveVersions = r)));
    await click(checkButton());
    // Both refetches are in flight now; the old stepper and timeline stay, the frame stays quiet.
    expect(api.assistPipeline).toHaveBeenCalledTimes(2);
    expect(container.textContent).not.toContain("Updating…");
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
    expect(container.querySelector("[data-refresh-content]").className).not.toContain("opacity-60");
    expect(container.querySelectorAll("#evolution-loop ol > li")).toHaveLength(8);
    expect(sectionOf("Version timeline").querySelectorAll("li")).toHaveLength(3);
    // The answers still land.
    await act(async () => {
      resolvePipeline(pipelineFixture({ cycle_note: "Late note." }));
      resolveVersions([...VERSIONS, versionRow(4, { origin: "manual", created_at: "2026-11-23T08:00:00" })]);
    });
    await settle();
    expect(container.querySelector("#evolution-loop").textContent).toContain("Late note.");
    expect(sectionOf("Version timeline").querySelectorAll("li")).toHaveLength(4);
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
  });

  it("does show Updating on an explicit Refresh, even when only the version list is slow", async () => {
    await renderPanel({ filters: FILTERS, reloadKey: 0 });
    let resolveVersions;
    api.getVersions.mockReturnValue(new Promise((r) => (resolveVersions = r)));
    await rerender(createElement(EvolutionPanel, { agentId: "assist-1", filters: FILTERS, reloadKey: 1 }));
    expect(container.textContent).toContain("Updating…");
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
    await act(async () => resolveVersions(VERSIONS));
    await settle();
    expect(container.textContent).not.toContain("Updating…");
  });

  it("does not read the pipeline again for a check that finds nothing", async () => {
    api.checkPattern.mockResolvedValue({ found: false, pattern_summary: null });
    await renderPanel();
    await click(checkButton());
    expect(api.assistPipeline).toHaveBeenCalledTimes(1);
  });
});

describe("Evolution tab: version timeline", () => {
  it("lists the live versions newest first and marks the current one", async () => {
    await renderPanel();
    const section = sectionOf("Version timeline");
    const items = [...section.querySelectorAll("li")];
    expect(items.map((li) => li.querySelector("[data-version-number]").textContent)).toEqual(["v3", "v2", "v1"]);
    expect(items[0].querySelector("[data-current]").textContent).toBe("Current");
    expect(items[0].textContent).toContain("2026-11-22 09:30 UTC");
    expect(items[1].querySelector("[data-current]")).toBeNull();
    expect(api.getVersions).toHaveBeenCalledWith("assist-1");
  });

  it("shows the empty state when the agent has no versions", async () => {
    api.getVersions.mockResolvedValue([]);
    await renderPanel();
    expect(sectionOf("Version timeline").textContent).toContain("No versions yet.");
  });

  it("shows an error with Retry that refetches", async () => {
    api.getVersions.mockRejectedValueOnce(new Error("Versions down")).mockResolvedValue(VERSIONS);
    await renderPanel();
    const section = sectionOf("Version timeline");
    expect(section.querySelector("[role='alert']").textContent).toContain("Couldn't load the version timeline: Versions down");
    expect(checkButton()).toBeDefined();
    await click(buttonByText(section, "Retry"));
    expect(sectionOf("Version timeline").querySelectorAll("li")).toHaveLength(3);
  });
});

describe("Evolution tab: before and after replay chart", () => {
  async function tableRows() {
    const section = sectionOf("Before and after replay");
    await click(buttonByText(section, "View as table"));
    return [...section.querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((c) => c.textContent));
  }

  it("is built from the stored outcome of each verified version, and nothing else", async () => {
    await renderPanel();
    // Only the two versions that were verified are read, as plain reads (not a report view).
    expect(api.getReport.mock.calls.map((c) => c.join("|")).sort()).toEqual(["pv2", "pv3"]);
    expect(await tableRows()).toEqual([
      ["v2", "1", "0", "2"],
      ["v3", "1", "2", "2"],
    ]);
    expect(sectionOf("Before and after replay").querySelectorAll(".recharts-bar-rectangle").length).toBeGreaterThan(0);
  });

  it("shows the empty state when no version was verified", async () => {
    api.getVersions.mockResolvedValue([versionRow(1, { status: "promoted", is_current: true })]);
    await renderPanel();
    expect(api.getReport).not.toHaveBeenCalled();
    const section = sectionOf("Before and after replay");
    expect(section.textContent).toContain("No replay results yet.");
    expect(section.querySelector("svg")).toBeNull();
  });

  it("shows the empty state when the verified versions have no stored outcome table", async () => {
    api.getReport.mockImplementation(async (id) => ({ ...REPORTS[id], outcome: null }));
    await renderPanel();
    expect(sectionOf("Before and after replay").textContent).toContain("No replay results yet.");
  });

  it("shows an error with Retry when a report cannot be read", async () => {
    api.getReport.mockRejectedValueOnce(new Error("Report down"));
    await renderPanel();
    const section = sectionOf("Before and after replay");
    expect(section.querySelector("[role='alert']").textContent).toContain("Couldn't load the replay results: Report down");
    api.getReport.mockImplementation(async (id) => REPORTS[id]);
    await click(buttonByText(section, "Retry"));
    expect((await tableRows()).length).toBe(2);
  });

  it("shows the version error, not an empty chart, when the versions cannot load", async () => {
    api.getVersions.mockRejectedValue(new Error("Versions down"));
    await renderPanel();
    expect(sectionOf("Before and after replay").querySelector("[role='alert']").textContent).toContain("Versions down");
    expect(container.textContent).not.toContain("No replay results yet.");
  });

  it("never puts conversation text, rationales or criterion names on screen", async () => {
    await renderPanel();
    await tableRows();
    expect(container.textContent).not.toContain("SECRET");
  });
});

describe("Evolution tab: live data only", () => {
  it("shows no demo marker or number with the source filter on all, and reads no demo-capable endpoint", async () => {
    await renderPanel({ filters: { range: "all", source: "all", version: null }, reloadKey: 0 });
    await click(buttonByText(sectionOf("Before and after replay"), "View as table"));
    expect(container.textContent).not.toMatch(/demo/i);
    expect(api.assistOverview).not.toHaveBeenCalled();
    expect(api.assistTrends).not.toHaveBeenCalled();
    expect(api.assistMetrics).not.toHaveBeenCalled();
    expect(api.assistQuality).not.toHaveBeenCalled();
    expect(api.assistFlagged).not.toHaveBeenCalled();
  });

  it("lays out without fixed widths, so it can shrink to a narrow window", async () => {
    await renderPanel();
    const fixed = [...container.querySelectorAll("[class]")].filter((el) => /(^|\s)w-\[\d+px\]/.test(el.getAttribute("class")));
    expect(fixed).toEqual([]);
  });
});

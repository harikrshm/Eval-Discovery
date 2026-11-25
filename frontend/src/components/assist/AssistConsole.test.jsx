import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    assistStatus: vi.fn(),
    assistMetrics: vi.fn(),
    assistTrends: vi.fn(),
    assistQuality: vi.fn(),
    assistEvaluateNow: vi.fn(),
    assistFlagged: vi.fn(),
    assistInsights: vi.fn(),
    assistOverview: vi.fn(),
    assistOperations: vi.fn(),
    assistPipeline: vi.fn(),
    getVersions: vi.fn(),
    getReport: vi.fn(),
    getAgentState: vi.fn(),
    getTests: vi.fn(),
    getJob: vi.fn(),
    checkPattern: vi.fn(),
    discoverCriterion: vi.fn(),
  };
});

import * as api from "../../api";
import AssistConsole from "./AssistConsole";
import {
  STATUS,
  buttonByLabel,
  buttonByText,
  click,
  flaggedRun,
  metricsFixture,
  mount,
  operationsFixture,
  overviewFixture,
  pipelineFixture,
  pressKey,
  qualityFixture,
  trendsFixture,
  unmount,
} from "./testUtils";

let container;

async function renderConsole(onDisconnect = vi.fn()) {
  container = await mount(createElement(AssistConsole, { onDisconnect }));
  return container;
}

function tabs() {
  return [...container.querySelectorAll('[role="tab"]')];
}

function tabNamed(name) {
  return tabs().find((t) => t.textContent === name);
}

// The Adoption tab mounts when it is first opened, so its tests open it first.
async function renderAdoption() {
  await renderConsole();
  await click(tabNamed("Adoption"));
  return container;
}

// Waits (in short real steps, inside act) until `condition()` holds; fails after about 3 seconds.
async function until(condition) {
  for (let i = 0; i < 150 && !condition(); i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  }
  expect(condition()).toBe(true);
}

async function choose(select, value) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.resetAllMocks();
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  api.assistStatus.mockResolvedValue(STATUS);
  api.assistMetrics.mockResolvedValue(metricsFixture());
  api.assistFlagged.mockResolvedValue([]);
  api.assistTrends.mockResolvedValue(trendsFixture());
  api.assistQuality.mockResolvedValue(qualityFixture());
  api.assistOverview.mockResolvedValue(overviewFixture());
  api.assistPipeline.mockResolvedValue(pipelineFixture());
  api.assistOperations.mockResolvedValue(operationsFixture());
  api.getVersions.mockResolvedValue([]);
  api.getReport.mockResolvedValue(null);
});

afterEach(async () => {
  delete Element.prototype.scrollIntoView;
  await unmount();
});

describe("console header", () => {
  it("shows the data-source label prominently with the live and imported counts", async () => {
    await renderConsole();
    const note = container.querySelector('[role="note"]');
    expect(note.textContent).toContain("demo dataset");
    expect(note.textContent).toContain("Includes imported demo data");
    expect(note.textContent).toContain("not measured production usage");
    expect(note.textContent).not.toMatch(/most|majority|mostly/i);
    expect(container.textContent).toContain("Page views — Live: 0 · Imported: 20");
    expect(container.textContent).toContain("Questions — Live: 0 · Imported: 12");
    expect(container.textContent).toContain("Patent Assist · v2");
  });

  it("shows no data-source label when the API sends none", async () => {
    api.assistMetrics.mockResolvedValue(
      metricsFixture({
        data_source_label: null,
        sources: { page_views: { live: 4, imported: 0 }, questions: { live: 2, imported: 0 } },
      })
    );
    await renderConsole();
    expect(container.querySelector('[role="note"]')).toBeNull();
    expect(container.textContent).not.toContain("Includes imported demo data");
    expect(container.textContent).not.toContain("Data source:");
    expect(container.textContent).toContain("Page views — Live: 4 · Imported: 0");
  });

  it("shows a plain error and keeps Disconnect available when the console cannot load", async () => {
    api.assistStatus.mockRejectedValue(new api.ApiError("No assistant agent in this workspace.", 404));
    const onDisconnect = vi.fn();
    await renderConsole(onDisconnect);
    expect(container.querySelector('[role="alert"]').textContent).toContain("No assistant agent in this workspace.");
    await click(buttonByText(container, "Disconnect"));
    expect(onDisconnect).toHaveBeenCalledTimes(1);
  });

  it("uses tab and panel semantics", async () => {
    await renderConsole();
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    expect(container.querySelector('[role="tablist"]')).not.toBeNull();
    expect(tabs.map((t) => t.textContent)).toEqual(["Overview", "Adoption", "Quality", "Evolution", "Operations"]);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false", "false", "false"]);
    const panels = [...container.querySelectorAll('[role="tabpanel"]')];
    expect(panels.map((p) => p.hidden)).toEqual([false, true, true, true, true]);
    expect(tabs[0].getAttribute("aria-controls")).toBe(panels[0].id);
    expect(panels[0].getAttribute("aria-labelledby")).toBe(tabs[0].id);
  });
});

describe("Adoption tab in the shell", () => {
  it("'Review in Evolution' switches to the Evolution tab", async () => {
    api.assistFlagged.mockResolvedValue([flaggedRun()]);
    api.getAgentState.mockResolvedValue({
      agent: {
        id: "assist-1",
        name: "Patent Assist",
        user_role_label: "Searcher",
        system_prompt: "p",
        prompt_version_id: "pv1",
        prompt_version_number: 1,
      },
      run_count: 3,
      runs: [],
      criteria: [],
      pattern_summary: null,
    });
    api.getTests.mockResolvedValue({
      prompt_version_id: "pv1",
      prompt_version_number: 1,
      stale_count: 0,
      candidate: null,
      criteria: [],
    });
    await renderAdoption();
    await click(buttonByLabel(container, "Review in Evolution: conversation abcdef12"));
    expect(tabNamed("Evolution").getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("#assist-panel-evolution").hidden).toBe(false);
    expect(api.getAgentState).toHaveBeenCalledWith("assist-1");
    expect(buttonByText(container, "Check flagged conversations for a pattern")).toBeDefined();
  });
});

describe("five-tab shell", () => {
  it("opens on Overview and mounts the other tabs only when they are first opened", async () => {
    await renderConsole();
    expect(tabNamed("Overview").getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("#assist-panel-overview").hidden).toBe(false);
    expect(container.querySelector('[data-kpi="questions"]')).not.toBeNull();
    // Nothing of Adoption or Evolution has loaded yet.
    expect(api.assistFlagged).not.toHaveBeenCalled();
    expect(api.getAgentState).not.toHaveBeenCalled();
    expect(container.querySelector("#assist-panel-adoption").textContent).toBe("");
    expect(container.querySelector("#assist-panel-quality").textContent).toBe("");

    await click(tabNamed("Adoption"));
    expect(api.assistFlagged).toHaveBeenCalledTimes(1);
    // A tab that was opened stays mounted when another is selected.
    await click(tabNamed("Overview"));
    expect(container.querySelector("#assist-panel-adoption").hidden).toBe(true);
    expect(container.querySelector("#assist-panel-adoption").textContent).toContain("Feature coverage by section");
  });

  it("shows the Quality tab when it is opened, and the Operations tab only when it is opened", async () => {
    await renderConsole();
    expect(api.assistQuality).not.toHaveBeenCalled();
    await click(tabNamed("Quality"));
    const quality = container.querySelector("#assist-panel-quality");
    expect(quality.querySelector("#quality-judge")).not.toBeNull();
    expect(quality.querySelector('[data-kpi="judged"]')).not.toBeNull();
    expect(quality.textContent).not.toContain("Coming in the next task");
    expect(api.assistOperations).not.toHaveBeenCalled();
    expect(container.querySelector("#assist-panel-operations").textContent).toBe("");
    await click(tabNamed("Operations"));
    const operations = container.querySelector("#assist-panel-operations");
    expect(api.assistOperations).toHaveBeenCalledTimes(1);
    expect(api.assistOperations).toHaveBeenCalledWith({ range: "30d", source: "all", version: null });
    expect(operations.querySelector("#ops-latency")).not.toBeNull();
    expect(operations.querySelector("#ops-errors")).not.toBeNull();
    expect(operations.querySelector('[data-kpi="p95"]')).not.toBeNull();
    expect(operations.textContent).not.toContain("Coming in the next task");
  });

  it("has one insights brief, on Quality and not on Adoption", async () => {
    await renderConsole();
    await click(tabNamed("Adoption"));
    await click(tabNamed("Quality"));
    expect(container.querySelectorAll("#insights-heading")).toHaveLength(1);
    expect(container.querySelector("#assist-panel-quality #insights-heading")).not.toBeNull();
  });

  it("cycles through all five tabs with the arrow keys, Home and End", async () => {
    // Passing through Evolution mounts it, so its loads stay pending here.
    api.getAgentState.mockReturnValue(new Promise(() => {}));
    await renderConsole();
    const names = () => tabs().filter((t) => t.getAttribute("aria-selected") === "true").map((t) => t.textContent);
    const list = container.querySelector('[role="tablist"]');
    const order = ["Overview", "Adoption", "Quality", "Evolution", "Operations"];
    for (const next of [...order.slice(1), "Overview"]) {
      await pressKey(list, "ArrowRight");
      expect(names()).toEqual([next]);
      expect(document.activeElement).toBe(tabNamed(next));
    }
    await pressKey(list, "ArrowLeft");
    expect(names()).toEqual(["Operations"]);
    await pressKey(list, "Home");
    expect(names()).toEqual(["Overview"]);
    await pressKey(list, "End");
    expect(names()).toEqual(["Operations"]);
    // Only the selected tab is in the tab order.
    expect(tabs().map((t) => t.tabIndex)).toEqual([-1, -1, -1, -1, 0]);
  });

  it("scrolls its tab bar instead of the page on a narrow window", async () => {
    await renderConsole();
    expect(container.querySelector('[role="tablist"]').className).toContain("overflow-x-auto");
  });
});

describe("global controls", () => {
  const DEFAULTS = { range: "30d", source: "all", version: null };

  it("loads with the defaults and shows the badge, UTC note and amber demo note", async () => {
    await renderConsole();
    expect(api.assistOverview).toHaveBeenCalledWith(DEFAULTS);
    expect(api.assistMetrics).toHaveBeenCalledWith(DEFAULTS);
    expect(container.querySelector("[data-source-badge]").textContent).toBe("Data: Live + demo dataset");
    expect(container.querySelector('[role="note"]')).not.toBeNull();
    expect(container.textContent).toContain("UTC");
  });

  it("fetches the version list from the overview once per source, without the version", async () => {
    await renderConsole();
    const versionCalls = api.assistOverview.mock.calls.filter(([f]) => f.range === "24h");
    expect(versionCalls).toEqual([[{ range: "24h", source: "all", version: null }]]);
    const select = container.querySelector("select#console-version");
    expect([...select.options].map((o) => o.textContent)).toEqual(["All", "v1", "v2"]);
  });

  it("refetches with the new range and keeps source and version", async () => {
    await renderConsole();
    await choose(container.querySelector("select#console-version"), "2");
    expect(api.assistOverview).toHaveBeenLastCalledWith({ range: "30d", source: "all", version: 2 });
    await click(buttonByText(container, "7 days"));
    expect(api.assistOverview).toHaveBeenLastCalledWith({ range: "7d", source: "all", version: 2 });
    expect(api.assistMetrics).toHaveBeenLastCalledWith({ range: "7d", source: "all", version: 2 });
    // The version list is not refetched for a range or version change.
    expect(api.assistOverview.mock.calls.filter(([f]) => f.range === "24h")).toHaveLength(1);
  });

  it("changing the source clears the version and updates the badge, and the note follows the API", async () => {
    await renderConsole();
    await choose(container.querySelector("select#console-version"), "2");
    api.assistMetrics.mockResolvedValue(metricsFixture({ data_source_label: null }));
    await choose(container.querySelector("select#console-source"), "live");
    expect(api.assistOverview).toHaveBeenCalledWith({ range: "30d", source: "live", version: null });
    expect(api.assistMetrics).toHaveBeenLastCalledWith({ range: "30d", source: "live", version: null });
    expect(container.querySelector("[data-source-badge]").textContent).toBe("Data: Live only");
    expect(container.querySelector("select#console-version").value).toBe("");
    // No demo rows in what the API reports, so no amber note.
    expect(container.querySelector('[role="note"]')).toBeNull();
    await choose(container.querySelector("select#console-source"), "demo");
    expect(container.querySelector("[data-source-badge]").textContent).toBe("Data: Demo dataset");
  });

  it("keeps the amber demo note whenever the API says demo rows are included", async () => {
    await renderConsole();
    const note = container.querySelector('[role="note"]');
    expect(note.textContent).toContain("Includes imported demo data");
    await click(buttonByText(container, "7 days"));
    expect(container.querySelector('[role="note"]')).not.toBeNull();
  });

  it("Refresh refetches the data with the same filters", async () => {
    await renderConsole();
    const before = api.assistOverview.mock.calls.length;
    const statusBefore = api.assistStatus.mock.calls.length;
    await click(buttonByText(container, "Refresh data"));
    expect(api.assistOverview.mock.calls.length).toBeGreaterThan(before);
    expect(api.assistStatus.mock.calls.length).toBe(statusBefore + 1);
    const after = api.assistOverview.mock.calls.slice(before).map(([f]) => f);
    expect(after).toContainEqual({ range: "30d", source: "all", version: null });
    expect(after).toContainEqual({ range: "24h", source: "all", version: null });
  });

  afterEach(() => {
    delete Element.prototype.scrollIntoView;
  });

  it("an alert on Overview opens its tab and scrolls to its anchor", async () => {
    api.assistOverview.mockResolvedValue(
      overviewFixture({
        alerts: [
          { id: "error_rate", severity: "critical", title: "Error rate is high", detail: "12% of calls failed", tab: "operations", anchor: "ops-errors", demo: true },
        ],
      })
    );
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    await renderConsole();
    const alert = container.querySelector("[data-alert-strip] button");
    expect(alert.textContent).toContain("demo");
    await click(alert);
    expect(tabNamed("Operations").getAttribute("aria-selected")).toBe("true");
    expect(scroll).toHaveBeenCalled();
    // The Operations tab's own anchor, which exists from the moment the tab mounts (even while loading).
    expect(scroll.mock.contexts[0].id).toBe("ops-errors");
    expect(container.querySelector("#assist-panel-operations").contains(scroll.mock.contexts[0])).toBe(true);
  });

  it("an alert whose anchor does not exist still opens its tab and does not throw", async () => {
    api.assistOverview.mockResolvedValue(
      overviewFixture({
        alerts: [
          { id: "p95_latency", severity: "warning", title: "Slow answers", detail: "p95 is high", tab: "operations", anchor: "no-such-anchor", demo: false },
        ],
      })
    );
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    await renderConsole();
    await click(container.querySelector("[data-alert-strip] button"));
    expect(tabNamed("Operations").getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("#assist-panel-operations").hidden).toBe(false);
    expect(scroll).not.toHaveBeenCalled();
  });

  it("shows an Adoption error with Retry for the metrics only, and the rest of the tab keeps working", async () => {
    api.assistMetrics.mockRejectedValue(new api.ApiError("Boom", 500));
    await renderConsole();
    // The rest of the console is still usable.
    expect(container.querySelector('[data-kpi="questions"]')).not.toBeNull();
    await click(tabNamed("Adoption"));
    const panel = container.querySelector("#assist-panel-adoption");
    expect(panel.textContent).toContain("Couldn't load the adoption numbers");
    // The usage trend comes from another request and is still shown.
    expect(panel.querySelector('[data-kpi="thumbs_down_rate"]')).not.toBeNull();
    api.assistMetrics.mockResolvedValue(metricsFixture());
    await click(buttonByText(panel, "Retry"));
    expect(panel.textContent).toContain("Feature coverage by section");
  });
});

function versionCalls() {
  return api.assistOverview.mock.calls.filter(([f]) => f.range === "24h").map(([f]) => f);
}

function versionOptions() {
  return [...container.querySelector("select#console-version").options].map((o) => o.textContent);
}

describe("version list", () => {
  function overviewBySource(bySource) {
    api.assistOverview.mockImplementation(async (f) => overviewFixture({ versions: bySource[f.source] ?? [] }));
  }

  it("never offers the previous source's versions while the new source's list loads", async () => {
    overviewBySource({ all: [1, 2, 3], live: [2, 3] });
    await renderConsole();
    expect(versionOptions()).toEqual(["All", "v1", "v2", "v3"]);

    let release;
    api.assistOverview.mockImplementation(
      (f) =>
        f.range === "24h" && f.source === "demo"
          ? new Promise((resolve) => (release = resolve))
          : Promise.resolve(overviewFixture())
    );
    await choose(container.querySelector("select#console-source"), "demo");
    // The list that is on hand belongs to another source: not offered.
    expect(versionOptions()).toEqual(["All"]);
    await act(async () => release(overviewFixture({ versions: [1] })));
    expect(versionOptions()).toEqual(["All", "v1"]);
  });

  it("keeps the selected version in the list while the right list is not there", async () => {
    overviewBySource({ all: [1, 2, 3] });
    await renderConsole();
    await choose(container.querySelector("select#console-version"), "2");
    expect(versionOptions()).toEqual(["All", "v1", "v2", "v3"]);
    // A refresh for the same source keeps the list it has.
    await click(buttonByText(container, "Refresh data"));
    expect(versionOptions()).toEqual(["All", "v1", "v2", "v3"]);
  });

  it("says so when the versions cannot be loaded, offers only the selected version, and Retry refetches", async () => {
    overviewBySource({ all: [1, 2, 3] });
    await renderConsole();
    await choose(container.querySelector("select#console-version"), "2");
    api.assistOverview.mockImplementation(async (f) => {
      if (f.range === "24h") throw new api.ApiError("Boom", 500);
      return overviewFixture();
    });
    await click(buttonByText(container, "Refresh data"));
    const message = [...container.querySelectorAll('[role="alert"]')].find((a) => a.textContent.includes("Couldn't load versions"));
    expect(message).toBeDefined();
    expect(versionOptions()).toEqual(["All", "v2"]);

    api.assistOverview.mockImplementation(async (f) => overviewFixture({ versions: f.range === "24h" ? [1, 2, 3, 4] : [1] }));
    const before = versionCalls().length;
    await click(buttonByText(message, "Retry"));
    expect(versionCalls().length).toBe(before + 1);
    expect(container.textContent).not.toContain("Couldn't load versions");
    expect(versionOptions()).toEqual(["All", "v1", "v2", "v3", "v4"]);
  });
});

describe("updating state", () => {
  it("marks the Overview busy with 'Updating…' after a filter change until the new data arrives", async () => {
    await renderConsole();
    const panel = () => container.querySelector("#assist-panel-overview");
    expect(panel().querySelector('[aria-busy="true"]')).toBeNull();
    let release;
    api.assistOverview.mockReturnValue(new Promise((resolve) => (release = resolve)));
    await click(buttonByText(container, "7 days"));
    expect(panel().querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(panel().textContent).toContain("Updating…");
    expect(container.querySelector("[data-source-badge]").textContent).toBe("Data: Live + demo dataset");
    await act(async () => release(overviewFixture()));
    expect(panel().querySelector('[aria-busy="true"]')).toBeNull();
    expect(panel().textContent).not.toContain("Updating…");
  });

  it("marks the Adoption tab and the demo note busy while the metrics reload, and clears both after", async () => {
    await renderAdoption();
    let release;
    api.assistMetrics.mockReturnValue(new Promise((resolve) => (release = resolve)));
    await click(buttonByText(container, "7 days"));
    const adoption = container.querySelector("#assist-panel-adoption");
    expect(adoption.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(adoption.textContent).toContain("Updating…");
    const note = container.querySelector('[role="note"]');
    expect(note.closest('[aria-busy="true"]').textContent).toContain("Updating…");
    expect(container.querySelector('[aria-busy="true"] [role="note"]')).not.toBeNull();
    await act(async () => release(metricsFixture()));
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    expect(container.textContent).not.toContain("Updating…");
  });

  it("sends the console filters, range and version included, to the Adoption tab's requests", async () => {
    await renderAdoption();
    await click(buttonByText(container, "7 days"));
    await choose(container.querySelector("select#console-source"), "demo");
    expect(api.assistTrends).toHaveBeenLastCalledWith({ range: "7d", source: "demo", version: null });
    expect(api.assistFlagged).toHaveBeenLastCalledWith({
      intent: undefined,
      section: undefined,
      limit: 50,
      range: "7d",
      source: "demo",
      version: null,
    });
  });

  it("an alert that points at the re-ask chart opens Adoption and scrolls to it", async () => {
    api.assistOverview.mockResolvedValue(
      overviewFixture({
        alerts: [
          { id: "reask", severity: "warning", title: "Re-asks are high", detail: "30% re-asked", tab: "adoption", anchor: "adoption-reasks", demo: false },
        ],
      })
    );
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    await renderConsole();
    await click(container.querySelector("[data-alert-strip] button"));
    expect(tabNamed("Adoption").getAttribute("aria-selected")).toBe("true");
    // The panel loads after the tab opens, so the shell looks for the anchor again; wait for that, not a fixed time.
    await until(() => scroll.mock.contexts.some((el) => el?.id === "adoption-reasks"));
  });

  it("sends the console filters to the Quality tab's requests", async () => {
    await renderConsole();
    await click(tabNamed("Quality"));
    await click(buttonByText(container, "7 days"));
    await choose(container.querySelector("select#console-source"), "live");
    expect(api.assistQuality).toHaveBeenLastCalledWith({ range: "7d", source: "live", version: null });
    expect(api.assistFlagged).toHaveBeenLastCalledWith({
      reason: undefined,
      limit: 50,
      range: "7d",
      source: "live",
      version: null,
    });
  });

  it("an alert about the judge backlog opens Quality and scrolls to the judge section", async () => {
    api.assistOverview.mockResolvedValue(
      overviewFixture({
        alerts: [
          { id: "judge_backlog", severity: "warning", title: "Judge backlog", detail: "30 waiting", tab: "quality", anchor: "quality-judge", demo: false },
        ],
      })
    );
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    await renderConsole();
    await click(container.querySelector("[data-alert-strip] button"));
    expect(tabNamed("Quality").getAttribute("aria-selected")).toBe("true");
    await until(() => scroll.mock.contexts.some((el) => el?.id === "quality-judge"));
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
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
  overviewFixture,
  pressKey,
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

async function choose(select, value) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function row(label) {
  return [...container.querySelectorAll("li")].find((li) => li.textContent.startsWith(label));
}

function tableRows(index) {
  return [...container.querySelectorAll("table")[index].querySelectorAll("tbody tr")].map((tr) =>
    [...tr.children].map((c) => c.textContent)
  );
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  vi.resetAllMocks();
  window.matchMedia = vi.fn(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  api.assistStatus.mockResolvedValue(STATUS);
  api.assistMetrics.mockResolvedValue(metricsFixture());
  api.assistFlagged.mockResolvedValue([]);
  api.assistOverview.mockResolvedValue(overviewFixture());
});

afterEach(unmount);

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

describe("adoption numbers", () => {
  it("draws coverage bars with a text value and an accessible label", async () => {
    await renderAdoption();
    const claims = row("claims");
    expect(claims.textContent).toContain("50%");
    const bar = claims.querySelector('[role="img"]');
    expect(bar.getAttribute("aria-label")).toBe("claims: 50%");
    expect(bar.querySelector("[data-bar-fill]").style.width).toBe("50%");
    // Every row of every bar list has exactly one labelled image that states a value or n/a.
    const rows = container.querySelectorAll('ul[aria-label^="Feature coverage"] > li, ul[aria-label^="Task mix"] > li');
    expect(rows.length).toBe(10);
    for (const li of rows) {
      expect(li.querySelectorAll('[role="img"]')).toHaveLength(1);
      expect(li.querySelector('[role="img"]').getAttribute("aria-label")).toMatch(/: (n\/a|(\d+, )?[\d.]+%)$/);
    }
  });

  it("renders a missing value as n/a with no bar", async () => {
    await renderAdoption();
    const classifications = row("classifications");
    expect(classifications.textContent).toContain("n/a");
    expect(classifications.querySelector("[data-bar-fill]")).toBeNull();
    expect(classifications.querySelector('[role="img"]').getAttribute("aria-label")).toBe("classifications: n/a");
    // A real zero is a zero, not n/a.
    expect(row("family").textContent).toContain("0%");
    expect(row("family").querySelector("[data-bar-fill]")).not.toBeNull();
    // Task mix with a null share.
    expect(row("other").querySelector("[data-bar-fill]")).toBeNull();
  });

  it("lists the task mix with counts and shares", async () => {
    await renderAdoption();
    expect(row("claim explain").textContent).toContain("6 · 50%");
    expect(row("claim explain").querySelector('[role="img"]').getAttribute("aria-label")).toBe("claim explain: 6, 50%");
  });

  it("shows the help-demand table with n/a and the questions without views", async () => {
    await renderAdoption();
    const rows = tableRows(0);
    expect(rows[0]).toEqual(["claims", "10", "30"]);
    expect(rows[2]).toEqual(["classifications", "0", "n/a"]);
    expect(rows[3]).toEqual(["citations", "5", "120"]); // help demand can exceed 100
    expect(container.textContent).toContain("Questions about sections with no recorded views: landscapes (2)");
  });

  it("shows the struggle numbers with n/a for missing rates", async () => {
    await renderAdoption();
    const dl = container.querySelector("dl");
    expect([...dl.children].map((c) => c.textContent)).toEqual([
      "Navigation help questions",
      "3",
      "Refusals",
      "1",
      "Unverified citation rate",
      "n/a",
      "Bounce rate",
      "20%",
    ]);
    const reask = tableRows(1);
    expect(reask[0]).toEqual(["claim explain", "33.3%", "16.7%"]);
    expect(reask[1]).toEqual(["navigation help", "n/a", "n/a"]);
  });

  it("states the metric definitions in plain text", async () => {
    await renderAdoption();
    const text = container.querySelector('section[aria-labelledby="definitions-heading"]').textContent;
    expect(text).toContain("at least 3 seconds");
    expect(text).toContain("per 100 non-bounce views in which that section was visible at all");
    expect(text).toContain("can exceed 100");
    expect(text).toContain('"down" ratings only');
    expect(text).toContain("legal-opinion questions");
    expect(text).toContain("Widget usage is NOT measured");
  });
});

describe("version comparison", () => {
  it("gives one sentence and no table when it is not available", async () => {
    await renderAdoption();
    const section = container.querySelector('section[aria-labelledby="versions-heading"]');
    expect(section.textContent).toContain("All questions are imported demo data, so versions cannot be compared.");
    expect(section.textContent).toContain("Real (non-imported) questions only.");
    expect(section.querySelector("table")).toBeNull();
  });

  it("shows per-version rows and greys versions with too little data", async () => {
    api.assistMetrics.mockResolvedValue(
      metricsFixture({
        data_source_label: null,
        version_comparison: {
          available: true,
          note: "Real (non-imported) questions only.",
          versions: {
            1: { questions: 12, reask_rate: 0.25, thumbs_down_rate: 0.1667, enough_data: true, intents: {} },
            2: { questions: 14, reask_rate: 0.1, thumbs_down_rate: 0, enough_data: true, intents: {} },
            3: { questions: 4, reask_rate: 1, thumbs_down_rate: 1, enough_data: false, intents: {} },
          },
        },
      })
    );
    await renderAdoption();
    const section = container.querySelector('section[aria-labelledby="versions-heading"]');
    const rows = [...section.querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toBe("v11225%16.7%");
    expect(rows[0].getAttribute("data-enough-data")).toBe("true");
    expect(rows[2].getAttribute("data-enough-data")).toBe("false");
    expect(rows[2].className).toContain("opacity-60");
    expect(rows[2].textContent).toContain("too few questions to compare");
    expect(rows[2].textContent).not.toContain("100%");
    expect(section.textContent).toContain("Real (non-imported) questions only.");
  });
});

describe("flagged conversations", () => {
  const flaggedItems = () => [...container.querySelectorAll('section[aria-labelledby="flagged-heading"] > ul > li')];

  it("lists imported and live conversations with a plain-text source tag", async () => {
    api.assistFlagged.mockResolvedValue([
      flaggedRun(),
      flaggedRun({ run_id: "run-2", source: "assist", session_id: null, patent_number: null, intents: [], reasons: [] }),
    ]);
    await renderAdoption();
    expect(api.assistFlagged).toHaveBeenCalledWith({ intent: undefined, section: undefined, limit: 50 });
    const items = flaggedItems();
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("imported");
    expect(items[0].textContent).toContain("abcdef12");
    expect(items[0].textContent).not.toContain("abcdef1234567890");
    expect(items[0].textContent).toContain("US1234567B2");
    expect(items[0].textContent).toContain("claim explain");
    expect(items[0].textContent).toContain("rated down, reasked");
    expect(items[1].textContent).toContain("live");
    expect(items[1].textContent).not.toContain("imported");
    expect(items[0].querySelector("a")).toBeNull();
  });

  it("filters the list when a help-demand section is clicked, and clears the filter", async () => {
    await renderAdoption();
    // Zero demand is not a hotspot.
    expect(buttonByLabel(container, "Show flagged conversations for section description")).toBeUndefined();
    api.assistFlagged.mockResolvedValue([flaggedRun()]);
    await click(buttonByLabel(container, "Show flagged conversations for section claims"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith({ intent: undefined, section: "claims", limit: 50 });
    expect(container.textContent).toContain("Filtered by section: claims");
    expect(flaggedItems()).toHaveLength(1);

    await click(buttonByText(container, "Clear filter"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith({ intent: undefined, section: undefined, limit: 50 });
    expect(container.textContent).not.toContain("Filtered by");
  });

  it("filters by intent from a re-ask hotspot", async () => {
    await renderAdoption();
    // A missing rate is not a hotspot.
    expect(buttonByLabel(container, "Show flagged conversations for task navigation help")).toBeUndefined();
    await click(buttonByLabel(container, "Show flagged conversations for task claim explain"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith({ intent: "claim_explain", section: undefined, limit: 50 });
    expect(container.textContent).toContain("Filtered by task: claim explain");
  });

  it("says so when nothing is flagged and shows a load error with a retry", async () => {
    await renderAdoption();
    expect(container.textContent).toContain("No flagged conversations.");
    api.assistFlagged.mockRejectedValueOnce(new api.ApiError("Boom", 500));
    await click(buttonByLabel(container, "Show flagged conversations for section claims"));
    expect(container.textContent).toContain("Couldn't load flagged conversations. Boom");
    await click(buttonByText(container, "Retry"));
    expect(container.textContent).toContain("No flagged conversations for this filter.");
  });

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
    expect(container.querySelector("#assist-panel-adoption").textContent).toContain("Feature coverage");
  });

  it("shows placeholders for Quality and Operations", async () => {
    await renderConsole();
    await click(tabNamed("Quality"));
    expect(container.querySelector("#assist-panel-quality").textContent).toContain("Coming in the next task");
    await click(tabNamed("Operations"));
    expect(container.querySelector("#assist-panel-operations").textContent).toContain("Coming in the next task");
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
    const target = document.createElement("div");
    target.id = "ops-errors";
    container.appendChild(target);
    const alert = container.querySelector("[data-alert-strip] button");
    expect(alert.textContent).toContain("demo");
    await click(alert);
    expect(tabNamed("Operations").getAttribute("aria-selected")).toBe("true");
    expect(scroll).toHaveBeenCalled();
    expect(scroll.mock.contexts[0]).toBe(target);
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

  it("shows an Adoption error with Retry when only the metrics fail", async () => {
    api.assistMetrics.mockRejectedValueOnce(new api.ApiError("Boom", 500));
    await renderConsole();
    // The rest of the console is still usable.
    expect(container.querySelector('[data-kpi="questions"]')).not.toBeNull();
    await click(tabNamed("Adoption"));
    const panel = container.querySelector("#assist-panel-adoption");
    expect(panel.textContent).toContain("Couldn't load the adoption numbers");
    await click(buttonByText(panel, "Retry"));
    expect(panel.textContent).toContain("Feature coverage");
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
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    assistMetrics: vi.fn(),
    assistTrends: vi.fn(),
    assistFlagged: vi.fn(),
    assistInsights: vi.fn(),
  };
});

import * as api from "../../api";
import AdoptionPanel from "./AdoptionPanel";
import {
  FILTERS,
  buttonByText,
  click,
  emptyMetrics,
  emptyTrends,
  flaggedRun,
  metricsFixture,
  mount,
  rerender,
  trendsFixture,
  unmount,
} from "./testUtils";

// jsdom has no layout, so Recharts' ResponsiveContainer would measure 0 x 0 and draw no bars. This
// observer reports a fixed size as soon as an element is observed.
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

// Every test renders real Recharts charts, and each bar pick re-renders them; under a loaded full run that
// can pass the default 5 s.
vi.setConfig({ testTimeout: 30000 });

let container;
let onOpenTab;

function props(overrides = {}) {
  return { filters: FILTERS, reloadKey: 0, onOpenTab, ...overrides };
}

async function renderPanel(overrides = {}) {
  container = await mount(createElement(AdoptionPanel, props(overrides)));
  return container;
}

function card(title) {
  return [...container.querySelectorAll("section")].find((s) => s.querySelector("h3")?.textContent === title);
}

async function tableOf(title) {
  const section = card(title);
  await click(buttonByText(section, "View as table"));
  return [...section.querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((c) => c.textContent));
}

function kpi(id) {
  return container.querySelector(`[data-kpi="${id}"]`);
}

function barButton(title, label) {
  return [...card(title).querySelectorAll('[role="button"]')].find((b) => b.getAttribute("aria-label") === label);
}

// What the panel asks `/flagged` for: the section or task picked plus the console's range, source and version.
function flaggedCall(over = {}, f = FILTERS) {
  return { intent: undefined, section: undefined, limit: 50, range: f.range, source: f.source, version: f.version, ...over };
}

function flaggedItems() {
  return [...container.querySelectorAll('section[aria-labelledby="flagged-heading"] > ul > li')];
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.ResizeObserver = SizedObserver;
  onOpenTab = vi.fn();
  api.assistMetrics.mockResolvedValue(metricsFixture());
  api.assistTrends.mockResolvedValue(trendsFixture());
  api.assistFlagged.mockResolvedValue([]);
});

afterEach(unmount);

describe("KPI row", () => {
  it("shows page views, questions and the two rates with their change versus the previous period", async () => {
    await renderPanel();
    expect(kpi("page_views").textContent).toContain("Page views");
    expect(kpi("page_views").textContent).toContain("20");
    expect(kpi("page_views").textContent).toContain("up 25% vs previous period");
    expect(kpi("questions").textContent).toContain("12");
    expect(kpi("questions").textContent).toContain("up 20% vs previous period");
    // 3 of 12 = 25% now against 2 of 10 = 20% before.
    expect(kpi("reask_rate").textContent).toContain("Re-ask rate");
    expect(kpi("reask_rate").textContent).toContain("25%");
    expect(kpi("reask_rate").textContent).toContain("up 5 pts vs previous period");
    // 2 of 12 = 16.7% now against 2 of 10 = 20% before.
    expect(kpi("thumbs_down_rate").textContent).toContain("Thumbs-down rate");
    expect(kpi("thumbs_down_rate").textContent).toContain("16.7%");
    expect(kpi("thumbs_down_rate").textContent).toContain("down 3.3 pts vs previous period");
  });

  it("marks falling re-asks as better and rising ones as worse, in words", async () => {
    await renderPanel();
    expect(kpi("reask_rate").textContent).toContain("(worse)");
    expect(kpi("thumbs_down_rate").textContent).toContain("(better)");
  });

  it("shows no change when there is no previous period", async () => {
    api.assistTrends.mockResolvedValue(trendsFixture({ previous: null }));
    await renderPanel();
    expect(container.querySelectorAll("[data-kpi]")).toHaveLength(4);
    expect(container.querySelector("[data-delta]")).toBeNull();
    expect(kpi("page_views").textContent).toContain("20");
  });

  it("shows no change for a count whose previous period was zero", async () => {
    api.assistTrends.mockResolvedValue(
      trendsFixture({ previous: { page_views: 0, questions: 0, reasked: 0, rated_down: 0 } })
    );
    await renderPanel();
    expect(kpi("page_views").querySelector("[data-delta]")).toBeNull();
    expect(kpi("questions").querySelector("[data-delta]")).toBeNull();
    // The rates have no previous value either (no questions then).
    expect(kpi("reask_rate").querySelector("[data-delta]")).toBeNull();
    expect(kpi("reask_rate").textContent).toContain("25%");
  });

  it("renders n/a, never 0%, for a rate with no questions", async () => {
    api.assistTrends.mockResolvedValue(
      trendsFixture({
        totals: { page_views: 9, questions: 0, reasked: 0, rated_down: 0 },
        points: [{ t: "2026-11-24T00:00:00", page_views: 9, questions: 0, reasked: 0, rated_down: 0 }],
      })
    );
    await renderPanel();
    expect(kpi("reask_rate").textContent).toContain("n/a");
    expect(kpi("reask_rate").textContent).not.toContain("0%");
    expect(kpi("reask_rate").querySelector("[data-delta]")).toBeNull();
    expect(kpi("thumbs_down_rate").textContent).toContain("n/a");
    expect(kpi("thumbs_down_rate").textContent).not.toContain("0%");
    expect(kpi("page_views").textContent).toContain("9");
  });

  it("shows the KPIs with the drop, not 'Not enough data', when this period is empty but the previous was not", async () => {
    api.assistTrends.mockResolvedValue(
      trendsFixture({
        totals: { page_views: 0, questions: 0, reasked: 0, rated_down: 0 },
        points: [{ t: "2026-11-24T00:00:00", page_views: 0, questions: 0, reasked: 0, rated_down: 0 }],
      })
    );
    await renderPanel();
    expect(container.querySelectorAll("[data-kpi]")).toHaveLength(4);
    expect(kpi("page_views").textContent).toContain("down 100% vs previous period");
    expect(kpi("questions").textContent).toContain("down 100% vs previous period");
    expect(kpi("reask_rate").textContent).toContain("n/a");
    expect(container.textContent.split("Not enough data yet.")).toHaveLength(1);
  });

  it("is empty only when this period and the previous one are both empty", async () => {
    api.assistTrends.mockResolvedValue(emptyTrends());
    await renderPanel();
    expect(container.querySelector("[data-kpi]")).toBeNull();
    expect(container.textContent).toContain("Not enough data yet.");
  });

  it("keeps the thumbs-down caveat as a short note under that KPI", async () => {
    await renderPanel();
    const note = kpi("thumbs_down_rate").querySelector("[data-kpi-note]");
    expect(note.textContent).toContain("one rating per conversation");
    expect(note.textContent).toContain("not comparable with earlier data");
    expect(kpi("reask_rate").querySelector("[data-kpi-note]")).toBeNull();
  });
});

describe("usage over time", () => {
  it("draws page views and questions with a UTC table and version markers, demo ones labelled", async () => {
    await renderPanel();
    const section = card("Page views and questions over time");
    expect(section.textContent).toContain("UTC");
    const legend = section.querySelector("[data-legend]").textContent;
    expect(legend).toContain("Page views");
    expect(legend).toContain("Questions");
    expect(legend).toContain("v2");
    expect(legend).toContain("v1 (demo)");
    expect(legend).not.toContain("v2 (demo)");
    expect(await tableOf("Page views and questions over time")).toEqual([
      ["2026-11-22 00:00 UTC", "5", "3"],
      ["2026-11-23 00:00 UTC", "7", "4"],
      ["2026-11-24 00:00 UTC", "8", "5"],
    ]);
  });
});

describe("chart panels", () => {
  it("lists feature coverage by section, with n/a for a missing value and 0% for a real zero", async () => {
    await renderPanel();
    const rows = await tableOf("Feature coverage by section");
    expect(rows[0]).toEqual(["claims", "50%"]);
    expect(rows[2]).toEqual(["classifications", "n/a"]);
    expect(rows[4]).toEqual(["family", "0%"]);
    expect(rows).toHaveLength(7);
  });

  it("lists help demand by section with views, n/a, and values above 100", async () => {
    await renderPanel();
    const rows = await tableOf("Help demand by section");
    expect(rows[0]).toEqual(["claims", "10", "30"]);
    expect(rows[2]).toEqual(["classifications", "0", "n/a"]);
    expect(rows[3]).toEqual(["citations", "5", "120"]);
    expect(container.textContent).toContain("Questions about sections with no recorded views: landscapes (2)");
  });

  it("lists the task mix with counts and shares", async () => {
    await renderPanel();
    const rows = await tableOf("Task mix");
    expect(rows[0]).toEqual(["claim explain", "6", "50%"]);
    expect(rows[1]).toEqual(["navigation help", "3", "25%"]);
    expect(rows[2]).toEqual(["other", "0", "n/a"]);
  });

  it("lists the re-ask and thumbs-down rate by task with n/a for a missing rate", async () => {
    await renderPanel();
    const rows = await tableOf("Re-ask and thumbs-down rate by task");
    expect(rows[0]).toEqual(["claim explain", "33.3%", "16.7%"]);
    expect(rows[1]).toEqual(["navigation help", "n/a", "n/a"]);
    expect(rows[2]).toEqual(["other", "0%", "n/a"]);
  });

  it("gives the section with the re-ask chart the id the alerts link to", async () => {
    await renderPanel();
    const target = container.querySelector("#adoption-reasks");
    expect(target).not.toBeNull();
    expect(target.querySelector("h3").textContent).toBe("Re-ask and thumbs-down rate by task");
  });

  it("keeps the struggle numbers, definitions and the version comparison", async () => {
    await renderPanel();
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
    const definitions = container.querySelector('section[aria-labelledby="definitions-heading"]').textContent;
    expect(definitions).toContain("at least 3 seconds");
    expect(definitions).toContain("can exceed 100");
    expect(definitions).toContain("Widget usage is NOT measured");
    const versions = container.querySelector('section[aria-labelledby="versions-heading"]');
    expect(versions.textContent).toContain("All questions are imported demo data, so versions cannot be compared.");
  });

  it("shows the version comparison table from the metrics when it is available", async () => {
    api.assistMetrics.mockResolvedValue(
      metricsFixture({
        version_comparison: {
          available: true,
          note: "Real (non-imported) questions only.",
          versions: { 1: { questions: 12, reask_rate: 0.25, thumbs_down_rate: 0.1667, enough_data: true, intents: {} } },
        },
      })
    );
    await renderPanel();
    const section = container.querySelector('section[aria-labelledby="versions-heading"]');
    expect(section.querySelector("tbody tr").textContent).toBe("v11225%16.7%");
    expect(section.textContent).toContain("Real (non-imported) questions only.");
  });

  it("keeps the definitions, then the version comparison, then the flagged list, and no insights panel", async () => {
    await renderPanel();
    const order = ["definitions-heading", "versions-heading", "flagged-heading"].map((id) =>
      [...container.querySelectorAll("h3")].findIndex((h) => h.id === id)
    );
    expect(order.every((n) => n >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    // The insights brief moved to the Quality tab, so there is one copy.
    expect(container.querySelector("#insights-heading")).toBeNull();
  });
});

describe("drill-down into flagged conversations", () => {
  it("makes every section and task bar a button inside an interactive chart group", async () => {
    await renderPanel();
    for (const title of [
      "Feature coverage by section",
      "Help demand by section",
      "Task mix",
      "Re-ask and thumbs-down rate by task",
    ]) {
      const group = card(title).querySelector('[role="group"]');
      expect(group, title).not.toBeNull();
      expect(card(title).querySelector('[role="img"]'), title).toBeNull();
      expect(group.querySelectorAll('[role="button"]').length, title).toBeGreaterThan(0);
    }
    // A bar with no value is not a button (nothing to see).
    expect(barButton("Feature coverage by section", "classifications: n/a")).toBeUndefined();
  });

  it("lists all flagged conversations first, asked with the console's source", async () => {
    api.assistFlagged.mockResolvedValue([
      flaggedRun(),
      flaggedRun({ run_id: "run-2", source: "assist", session_id: null, patent_number: null, intents: [], reasons: [] }),
    ]);
    await renderPanel({ filters: { ...FILTERS, source: "live" } });
    expect(api.assistFlagged).toHaveBeenCalledTimes(1);
    expect(api.assistFlagged).toHaveBeenCalledWith(flaggedCall({}, { ...FILTERS, source: "live" }));
    const items = flaggedItems();
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("imported");
    expect(items[0].textContent).toContain("abcdef12");
    expect(items[0].textContent).not.toContain("abcdef1234567890");
    expect(items[0].textContent).toContain("US1234567B2");
    expect(items[0].textContent).toContain("claim explain");
    expect(items[0].textContent).toContain("Thumbs-down, Re-ask");
    expect(items[1].textContent).toContain("live");
    expect(items[0].querySelector("a")).toBeNull();
  });

  it("selecting a section bar loads and shows the flagged conversations for that section, then clears", async () => {
    await renderPanel();
    api.assistFlagged.mockResolvedValue([flaggedRun({ run_id: "run-9", sections: ["claims"] })]);
    await click(barButton("Help demand by section", "claims: 30"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ section: "claims" }));
    expect(container.textContent).toContain("Filtered by section: claims");
    expect(flaggedItems()).toHaveLength(1);
    expect(flaggedItems()[0].textContent).toContain("claims");

    await click(buttonByText(container, "Clear filter"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall());
    expect(container.textContent).not.toContain("Filtered by");
  });

  it("the coverage and help-demand charts select a section", async () => {
    await renderPanel();
    await click(barButton("Feature coverage by section", "claims: 50%"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ section: "claims" }));
    await click(barButton("Help demand by section", "citations: 120"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ section: "citations" }));
  });

  it("the task mix and re-ask charts select an intent, replacing a section filter", async () => {
    await renderPanel();
    await click(barButton("Feature coverage by section", "claims: 50%"));
    await click(barButton("Task mix", "claim explain: 50%"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ intent: "claim_explain" }));
    expect(container.textContent).toContain("Filtered by task: claim explain");
    expect(container.textContent).not.toContain("Filtered by section");
  });

  it("the re-ask chart selects an intent from either of its two bars", async () => {
    await renderPanel();
    await click(barButton("Re-ask and thumbs-down rate by task", "claim explain, Thumbs-down rate: 16.7%"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ intent: "claim_explain" }));
  });

  it("selecting a bar moves focus to the flagged list heading", async () => {
    await renderPanel();
    await click(barButton("Task mix", "claim explain: 50%"));
    expect(document.activeElement).toBe(container.querySelector("#flagged-heading"));
  });

  it("says which range, source and version the list covers, in words, with or without a pick", async () => {
    const scoped = { range: "7d", source: "live", version: 3 };
    await renderPanel({ filters: scoped });
    const section = container.querySelector('section[aria-labelledby="flagged-heading"]');
    expect(section.textContent).toContain("last 7 days (UTC), source: live, version 3");
    expect(section.textContent).not.toContain("Filtered by");
    await click(barButton("Help demand by section", "claims: 30"));
    expect(section.textContent).toContain("Filtered by section: claims, last 7 days (UTC), source: live, version 3");
    await rerender(createElement(AdoptionPanel, props({ filters: { range: "all", source: "all", version: null } })));
    expect(section.textContent).toContain("Filtered by section: claims, all time, source: live and demo dataset");
    expect(section.textContent).not.toContain("version");
  });

  it("announces how many flagged conversations were found, in the polite status", async () => {
    api.assistFlagged.mockResolvedValue([flaggedRun(), flaggedRun({ run_id: "run-2" })]);
    await renderPanel();
    const status = container.querySelector('section[aria-labelledby="flagged-heading"] [role="status"]');
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.textContent).toBe("2 flagged conversations");
    api.assistFlagged.mockResolvedValue([flaggedRun()]);
    await click(barButton("Help demand by section", "claims: 30"));
    expect(status.textContent).toBe("1 flagged conversation");
    api.assistFlagged.mockResolvedValue([]);
    await click(barButton("Task mix", "claim explain: 50%"));
    expect(status.textContent).toBe("No flagged conversations for this selection.");
  });

  it("says so when nothing is flagged and shows a load error with a retry that refetches", async () => {
    await renderPanel();
    expect(container.textContent).toContain("No flagged conversations for this selection.");
    api.assistFlagged.mockRejectedValueOnce(new api.ApiError("Boom", 500));
    await click(barButton("Help demand by section", "claims: 30"));
    expect(container.textContent).toContain("Couldn't load flagged conversations. Boom");
    await click(buttonByText(container.querySelector('section[aria-labelledby="flagged-heading"]'), "Retry"));
    expect(container.textContent).toContain("No flagged conversations for this selection.");
  });

  it("'Review in Evolution' asks the shell to open the Evolution tab", async () => {
    api.assistFlagged.mockResolvedValue([flaggedRun()]);
    await renderPanel();
    await click(buttonByText(container, "Review in Evolution"));
    expect(onOpenTab).toHaveBeenCalledWith("evolution");
  });
});

describe("states", () => {
  it("shows 'Not enough data yet.' in the trend panel and the chart panel for an empty database", async () => {
    api.assistMetrics.mockResolvedValue(emptyMetrics());
    api.assistTrends.mockResolvedValue(emptyTrends());
    await renderPanel();
    const text = container.textContent;
    expect(text.split("Not enough data yet.")).toHaveLength(3);
    expect(container.querySelector("[data-kpi]")).toBeNull();
    expect(container.querySelector("table")).toBeNull();
    expect(text).not.toMatch(/NaN|undefined/);
    // The flagged list and the definitions are still there.
    expect(text).toContain("No flagged conversations for this selection.");
    expect(text).toContain("Widget usage is NOT measured");
  });

  it("shows 'Not enough data yet.' for an empty object payload", async () => {
    api.assistMetrics.mockResolvedValue({});
    api.assistTrends.mockResolvedValue({});
    await renderPanel();
    expect(container.textContent.split("Not enough data yet.")).toHaveLength(3);
  });

  it("shows loading status in each panel while the requests are pending", async () => {
    api.assistMetrics.mockReturnValue(new Promise(() => {}));
    api.assistTrends.mockReturnValue(new Promise(() => {}));
    await renderPanel();
    expect(container.querySelectorAll('[role="status"]').length).toBeGreaterThanOrEqual(2);
    expect(container.textContent).toContain("Loading");
  });

  it("an error in the metrics shows that panel's Retry and leaves the trend panel working", async () => {
    api.assistMetrics.mockRejectedValueOnce(new api.ApiError("Boom", 500));
    await renderPanel();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => a.textContent.includes("adoption numbers"));
    expect(alert.textContent).toContain("Couldn't load the adoption numbers: Boom");
    expect(container.querySelectorAll("[data-kpi]")).toHaveLength(4);
    expect(card("Page views and questions over time")).toBeDefined();
    expect(card("Feature coverage by section")).toBeUndefined();
    // The flagged list does not depend on the metrics.
    expect(container.textContent).toContain("No flagged conversations for this selection.");
    await click(buttonByText(alert, "Retry"));
    expect(api.assistMetrics).toHaveBeenCalledTimes(2);
    expect(card("Feature coverage by section")).toBeDefined();
  });

  it("an error in the trends shows that panel's Retry and leaves the charts working", async () => {
    api.assistTrends.mockRejectedValueOnce(new api.ApiError("Nope", 500));
    await renderPanel();
    const alert = [...container.querySelectorAll('[role="alert"]')].find((a) => a.textContent.includes("usage trend"));
    expect(alert.textContent).toContain("Couldn't load the usage trend: Nope");
    expect(container.querySelector("[data-kpi]")).toBeNull();
    expect(card("Feature coverage by section")).toBeDefined();
    await click(buttonByText(alert, "Retry"));
    expect(api.assistTrends).toHaveBeenCalledTimes(2);
    expect(container.querySelectorAll("[data-kpi]")).toHaveLength(4);
  });

  it("marks each panel busy with 'Updating…' while it reloads behind the numbers on screen", async () => {
    await renderPanel();
    let releaseTrends;
    let releaseMetrics;
    api.assistTrends.mockReturnValue(new Promise((resolve) => (releaseTrends = resolve)));
    api.assistMetrics.mockReturnValue(new Promise((resolve) => (releaseMetrics = resolve)));
    const next = props({ filters: { ...FILTERS, range: "7d" } });
    await rerender(createElement(AdoptionPanel, next));
    expect(container.querySelectorAll('[aria-busy="true"]')).toHaveLength(2);
    expect(container.textContent).toContain("Updating…");
    releaseTrends(trendsFixture());
    await rerender(createElement(AdoptionPanel, next));
    expect(container.querySelectorAll('[aria-busy="true"]')).toHaveLength(1);
    releaseMetrics(metricsFixture());
    await rerender(createElement(AdoptionPanel, next));
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    expect(container.textContent).not.toContain("Updating…");
  });
});

describe("requests", () => {
  it("asks metrics and trends with the shared filters", async () => {
    await renderPanel({ filters: { range: "7d", source: "demo", version: 2 } });
    expect(api.assistMetrics).toHaveBeenCalledWith({ range: "7d", source: "demo", version: 2 });
    expect(api.assistTrends).toHaveBeenCalledWith({ range: "7d", source: "demo", version: 2 });
  });

  it("refetches the metrics, the trends and the flagged list when the filters change", async () => {
    await renderPanel();
    const next = { range: "24h", source: "live", version: 3 };
    await rerender(createElement(AdoptionPanel, props({ filters: next })));
    expect(api.assistMetrics).toHaveBeenLastCalledWith(next);
    expect(api.assistTrends).toHaveBeenLastCalledWith(next);
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({}, next));
  });

  it("refetches the flagged list with the new range or version, keeping the picked section", async () => {
    await renderPanel();
    await click(barButton("Help demand by section", "claims: 30"));
    const week = { ...FILTERS, range: "7d" };
    await rerender(createElement(AdoptionPanel, props({ filters: week })));
    expect(api.assistFlagged).toHaveBeenCalledTimes(3);
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ section: "claims" }, week));
    const versioned = { ...week, version: 3 };
    await rerender(createElement(AdoptionPanel, props({ filters: versioned })));
    expect(api.assistFlagged).toHaveBeenCalledTimes(4);
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ section: "claims" }, versioned));
  });

  it("keeps the selected section when the source changes and refetches the list for it", async () => {
    await renderPanel();
    await click(barButton("Help demand by section", "claims: 30"));
    await rerender(createElement(AdoptionPanel, props({ filters: { ...FILTERS, source: "demo" } })));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ section: "claims" }, { ...FILTERS, source: "demo" }));
  });

  it("refetches everything when Refresh bumps the reload key", async () => {
    await renderPanel();
    await rerender(createElement(AdoptionPanel, props({ reloadKey: 1 })));
    expect(api.assistMetrics).toHaveBeenCalledTimes(2);
    expect(api.assistTrends).toHaveBeenCalledTimes(2);
    expect(api.assistFlagged).toHaveBeenCalledTimes(2);
  });
});

describe("text only", () => {
  it("shows counts, rates, ids and labels and never question or answer text", async () => {
    api.assistFlagged.mockResolvedValue([flaggedRun({ questions: [{ text: "SECRET QUESTION TEXT" }] })]);
    await renderPanel();
    expect(container.textContent).not.toContain("SECRET");
  });
});

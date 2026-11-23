import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    assistStatus: vi.fn(),
    assistMetrics: vi.fn(),
    assistFlagged: vi.fn(),
    assistInsights: vi.fn(),
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
  unmount,
} from "./testUtils";

let container;

async function renderConsole(onDisconnect = vi.fn()) {
  container = await mount(createElement(AssistConsole, { onDisconnect }));
  return container;
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
    expect(container.textContent).not.toContain("demo dataset");
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
    expect(tabs.map((t) => t.textContent)).toEqual(["Adoption", "Evolution"]);
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    const panels = [...container.querySelectorAll('[role="tabpanel"]')];
    expect(panels[0].hidden).toBe(false);
    expect(panels[1].hidden).toBe(true);
    expect(tabs[0].getAttribute("aria-controls")).toBe(panels[0].id);
    expect(panels[0].getAttribute("aria-labelledby")).toBe(tabs[0].id);
  });
});

describe("adoption numbers", () => {
  it("draws coverage bars with a text value and an accessible label", async () => {
    await renderConsole();
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
    await renderConsole();
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
    await renderConsole();
    expect(row("claim explain").textContent).toContain("6 · 50%");
    expect(row("claim explain").querySelector('[role="img"]').getAttribute("aria-label")).toBe("claim explain: 6, 50%");
  });

  it("shows the help-demand table with n/a and the questions without views", async () => {
    await renderConsole();
    const rows = tableRows(0);
    expect(rows[0]).toEqual(["claims", "10", "30"]);
    expect(rows[2]).toEqual(["classifications", "0", "n/a"]);
    expect(rows[3]).toEqual(["citations", "5", "120"]); // help demand can exceed 100
    expect(container.textContent).toContain("Questions about sections with no recorded views: landscapes (2)");
  });

  it("shows the struggle numbers with n/a for missing rates", async () => {
    await renderConsole();
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
    await renderConsole();
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
    await renderConsole();
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
    await renderConsole();
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
    await renderConsole();
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
    await renderConsole();
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
    await renderConsole();
    // A missing rate is not a hotspot.
    expect(buttonByLabel(container, "Show flagged conversations for task navigation help")).toBeUndefined();
    await click(buttonByLabel(container, "Show flagged conversations for task claim explain"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith({ intent: "claim_explain", section: undefined, limit: 50 });
    expect(container.textContent).toContain("Filtered by task: claim explain");
  });

  it("says so when nothing is flagged and shows a load error with a retry", async () => {
    await renderConsole();
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
    await renderConsole();
    await click(buttonByLabel(container, "Review in Evolution: conversation abcdef12"));
    const tabs = [...container.querySelectorAll('[role="tab"]')];
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(container.querySelector("#assist-panel-evolution").hidden).toBe(false);
    expect(api.getAgentState).toHaveBeenCalledWith("assist-1");
    expect(buttonByText(container, "Check flagged conversations for a pattern")).toBeDefined();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    assistQuality: vi.fn(),
    assistTrends: vi.fn(),
    assistFlagged: vi.fn(),
    assistEvaluateNow: vi.fn(),
    assistInsights: vi.fn(),
  };
});

import * as api from "../../api";
import QualityTab from "./QualityTab";
import {
  FILTERS,
  buttonByText,
  click,
  emptyQuality,
  flaggedRun,
  mount,
  qualityFixture,
  rerender,
  trendsFixture,
  unmount,
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

// Every test renders real Recharts charts; under a loaded full run that can pass the default 5 s.
vi.setConfig({ testTimeout: 30000 });

let container;
let onOpenTab;
let originalObserver;

function props(overrides = {}) {
  return { filters: FILTERS, reloadKey: 0, onOpenTab, ...overrides };
}

async function renderTab(overrides = {}) {
  container = await mount(createElement(QualityTab, props(overrides)));
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

function chips() {
  return [...container.querySelectorAll('[role="group"][aria-label="Flagged conversations by reason"] button')];
}

function chip(name) {
  return chips().find((b) => b.textContent === name);
}

function flaggedItems() {
  return [...container.querySelectorAll('section[aria-labelledby="flagged-heading"] ul > li')];
}

function flaggedCall(over = {}, f = FILTERS) {
  return { limit: 50, range: f.range, source: f.source, version: f.version, ...over };
}

beforeEach(() => {
  vi.resetAllMocks();
  onOpenTab = vi.fn();
  originalObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = SizedObserver;
  api.assistQuality.mockResolvedValue(qualityFixture());
  api.assistTrends.mockResolvedValue(trendsFixture());
  api.assistFlagged.mockResolvedValue([]);
  api.assistEvaluateNow.mockResolvedValue({ queued: 0, waiting: 0 });
});

afterEach(async () => {
  await unmount();
  globalThis.ResizeObserver = originalObserver;
});

describe("headline numbers", () => {
  it("shows the four judge numbers, with n/a for an unknown share", async () => {
    await renderTab();
    expect(kpi("judged").textContent).toContain("Judged conversations");
    expect(kpi("judged").textContent).toContain("8");
    // The average is weighted by how many conversations each bucket judged: (3 x 6 + 4 x 2) / 8.
    expect(kpi("avg_grade").textContent).toContain("3.25");
    expect(kpi("concise_share").textContent).toContain("75");
    expect(kpi("concise_share").textContent).toContain("%");
    expect(kpi("citations_share").textContent).toContain("n/a");
    expect(kpi("citations_share").textContent).not.toContain("0%");
  });

  it("shows n/a for the average grade when no bucket has a grade, never 0", async () => {
    api.assistQuality.mockResolvedValue(
      qualityFixture({ judged: 0, unjudged: 2, grade_trend: [{ t: "2026-11-22T00:00:00", avg_grade: null, n: 0 }], concise_share: null })
    );
    await renderTab();
    expect(kpi("avg_grade").textContent).toContain("n/a");
    expect(kpi("avg_grade").textContent).not.toMatch(/\b0\b/);
    expect(kpi("concise_share").textContent).toContain("n/a");
  });

  it("says how many conversations are still unjudged", async () => {
    await renderTab();
    expect(container.querySelector("[data-unjudged]").textContent).toBe("3 conversations are still unjudged.");
    api.assistQuality.mockResolvedValue(qualityFixture({ unjudged: 1 }));
    await rerender(createElement(QualityTab, props({ reloadKey: 1 })));
    expect(container.querySelector("[data-unjudged]").textContent).toBe("1 conversation is still unjudged.");
    api.assistQuality.mockResolvedValue(qualityFixture({ unjudged: 0 }));
    await rerender(createElement(QualityTab, props({ reloadKey: 2 })));
    expect(container.querySelector("[data-unjudged]").textContent).toBe("No conversations are waiting to be judged.");
  });

  it("says the judge numbers include the demo dataset unless the source is live", async () => {
    await renderTab();
    expect(container.querySelector("[data-demo-note]").textContent).toContain("includes demo dataset");
    await rerender(createElement(QualityTab, props({ filters: { ...FILTERS, source: "demo" } })));
    expect(container.querySelector("[data-demo-note]").textContent).toContain("includes demo dataset");
    await rerender(createElement(QualityTab, props({ filters: { ...FILTERS, source: "live" } })));
    expect(container.querySelector("[data-demo-note]")).toBeNull();
    expect(container.textContent).not.toContain("includes demo dataset");
  });

  it("states that the judge is an LLM's opinion that has not been checked against human raters", async () => {
    await renderTab();
    const note = container.querySelector("[data-judge-disclaimer]");
    expect(note.textContent).toContain("opinion of a language model");
    expect(note.textContent).toContain("not been validated against human raters");
  });
});

describe("charts and tables", () => {
  it("draws the grade trend with a gap, in UTC, and the version markers (demo labelled)", async () => {
    await renderTab();
    const rows = await tableOf("Average judge grade over time");
    expect(rows).toEqual([
      ["2026-11-22 00:00 UTC", "3", "6"],
      ["2026-11-23 00:00 UTC", "n/a", "0"],
      ["2026-11-24 00:00 UTC", "4", "2"],
    ]);
    const legend = card("Average judge grade over time").querySelector("[data-legend]").textContent;
    expect(legend).toContain("v2");
    expect(legend).toContain("v1 (demo)");
    expect(legend.match(/\(demo\)/g)).toHaveLength(1);
    // The empty bucket is a gap in the line, not a point at zero.
    expect(card("Average judge grade over time").querySelectorAll(".recharts-line-curve").length).toBe(1);
  });

  it("still draws the trend when the version markers cannot be loaded", async () => {
    api.assistTrends.mockRejectedValue(new Error("boom"));
    await renderTab();
    expect(card("Average judge grade over time")).toBeTruthy();
    expect(card("Average judge grade over time").querySelector("[data-legend]").textContent).not.toContain("v2");
    expect(container.textContent).not.toContain("boom");
  });

  it("says so, instead of an empty chart, when nothing in the range was graded", async () => {
    api.assistQuality.mockResolvedValue(
      qualityFixture({ grade_trend: [{ t: "2026-11-22T00:00:00", avg_grade: null, n: 0 }] })
    );
    await renderTab();
    expect(container.textContent).toContain("No graded conversations in this range yet.");
    expect(card("Average judge grade over time")?.querySelector("table, svg")).toBeFalsy();
  });

  it("breaks the verdicts down by answered and legal handling", async () => {
    await renderTab();
    expect(await tableOf("Verdicts")).toEqual([
      ["Answered: yes", "5"],
      ["Answered: partly", "2"],
      ["Answered: no", "1"],
      ["Legal question: handled", "2"],
      ["Legal question: mishandled", "1"],
    ]);
  });

  it("shows the reasons with readable labels, all in one map", async () => {
    await renderTab();
    expect(await tableOf("Reasons")).toEqual([
      ["No issue found", "4"],
      ["Missed the question", "2"],
      ["Off topic", "0"],
      ["Too long", "1"],
      ["Unsupported claim", "0"],
      ["Bad citation", "1"],
      ["Gave legal advice", "0"],
      ["Mishandled a refusal", "0"],
      ["Other", "0"],
    ]);
    expect(container.textContent).not.toMatch(/missed_question|no_issue|bad_citation|legal_advice_given/);
  });

  it("maps an unknown reason to Other, adds it to the fixed Other, and never prints the raw value", async () => {
    api.assistQuality.mockResolvedValue(
      qualityFixture({
        reasons: { ...qualityFixture().reasons, other: 1, "<script>alert(1)</script>": 2, mystery_reason: 3 },
      })
    );
    await renderTab();
    const rows = await tableOf("Reasons");
    expect(rows.filter(([name]) => name === "Other")).toEqual([["Other", "6"]]);
    expect(container.innerHTML).not.toContain("<script>");
    expect(container.textContent).not.toContain("mystery_reason");
    expect(container.textContent).not.toContain("alert(1)");
  });

  it("shows the rating distribution", async () => {
    await renderTab();
    expect(await tableOf("Ratings")).toEqual([
      ["Thumbs-up", "3"],
      ["Thumbs-down", "2"],
      ["No answer", "1"],
      ["No rating", "6"],
    ]);
  });

  it("shows no verdict or reason charts when nothing was judged, but still the ratings", async () => {
    api.assistQuality.mockResolvedValue(qualityFixture({ judged: 0, unjudged: 4, answered: { yes: 0, partly: 0, no: 0 }, legal: { handled: 0, mishandled: 0, not_applicable: 0 }, grade_trend: [], reasons: emptyQuality().reasons }));
    await renderTab();
    expect(container.textContent).toContain("Not enough data yet.");
    expect(card("Verdicts")?.querySelector("table")).toBeFalsy();
    expect(card("Ratings")).toBeTruthy();
    expect(container.querySelector("[data-unjudged]").textContent).toContain("4 conversations");
  });

  it("makes every chart a labelled group with a table equivalent, not an interactive chart", async () => {
    await renderTab();
    for (const title of ["Average judge grade over time", "Verdicts", "Reasons", "Ratings"]) {
      const wrapper = card(title).querySelector('[role="img"]');
      expect(wrapper, title).toBeTruthy();
      expect(card(title).querySelector('[role="button"]')).toBeNull();
    }
  });
});

describe("requests", () => {
  it("asks for the quality numbers, the markers and the flagged list with the console filters", async () => {
    const filters = { range: "7d", source: "live", version: 2 };
    await renderTab({ filters });
    expect(api.assistQuality).toHaveBeenCalledWith(filters);
    expect(api.assistTrends).toHaveBeenCalledWith(filters);
    expect(api.assistFlagged).toHaveBeenCalledWith(flaggedCall({}, filters));
  });

  it("refetches when the filters or the reload key change", async () => {
    await renderTab();
    await rerender(createElement(QualityTab, props({ filters: { ...FILTERS, range: "24h" } })));
    expect(api.assistQuality).toHaveBeenLastCalledWith({ ...FILTERS, range: "24h" });
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({}, { ...FILTERS, range: "24h" }));
    const before = api.assistQuality.mock.calls.length;
    await rerender(createElement(QualityTab, props({ filters: { ...FILTERS, range: "24h" }, reloadKey: 1 })));
    expect(api.assistQuality.mock.calls.length).toBe(before + 1);
  });
});

describe("flagged conversations and reason chips", () => {
  it("has the six chips, All first and selected", async () => {
    await renderTab();
    expect(chips().map((b) => b.textContent)).toEqual([
      "All",
      "Thumbs-down",
      "No answer",
      "Re-ask",
      "Citation failure",
      "Auto-judge",
    ]);
    expect(chip("All").getAttribute("aria-pressed")).toBe("true");
    expect(chip("Auto-judge").getAttribute("aria-pressed")).toBe("false");
  });

  it.each([
    ["Thumbs-down", "rated_down"],
    ["No answer", "no_answer"],
    ["Re-ask", "reasked"],
    ["Citation failure", "citation_unverified"],
    ["Auto-judge", "auto_judge"],
  ])("the %s chip asks for reason %s with the console filters", async (name, reason) => {
    const filters = { range: "7d", source: "live", version: 3 };
    await renderTab({ filters });
    await click(chip(name));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ reason }, filters));
    expect(chip(name).getAttribute("aria-pressed")).toBe("true");
    expect(chip("All").getAttribute("aria-pressed")).toBe("false");
  });

  it("the All chip asks without a reason again", async () => {
    await renderTab();
    await click(chip("Re-ask"));
    await click(chip("All"));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall());
    expect(api.assistFlagged.mock.calls.at(-1)[0].reason).toBeUndefined();
  });

  it("keeps the picked chip when the filters change", async () => {
    await renderTab();
    await click(chip("Re-ask"));
    await rerender(createElement(QualityTab, props({ filters: { ...FILTERS, range: "7d" } })));
    expect(api.assistFlagged).toHaveBeenLastCalledWith(flaggedCall({ reason: "reasked" }, { ...FILTERS, range: "7d" }));
    expect(chip("Re-ask").getAttribute("aria-pressed")).toBe("true");
  });

  it("the Auto-judge chip shows a row labelled Auto-judge", async () => {
    await renderTab();
    api.assistFlagged.mockResolvedValue([flaggedRun({ run_id: "run-j", reasons: ["auto_judge"], source: "assist" })]);
    await click(chip("Auto-judge"));
    const items = flaggedItems();
    expect(items).toHaveLength(1);
    expect(items[0].textContent).toContain("Auto-judge");
    expect(items[0].textContent).not.toContain("auto_judge");
    expect(items[0].textContent).not.toContain("auto judge");
  });

  it("labels every flag reason in words and never prints a raw reason", async () => {
    api.assistFlagged.mockResolvedValue([
      flaggedRun({ reasons: ["rated_down", "no_answer", "reasked", "citation_unverified", "auto_judge", "<b>new_reason</b>"] }),
    ]);
    await renderTab();
    const text = flaggedItems()[0].textContent;
    expect(text).toContain("Thumbs-down, No answer, Re-ask, Citation failure, Auto-judge, Other");
    expect(text).not.toContain("new_reason");
    expect(flaggedItems()[0].innerHTML).not.toContain("<b>");
  });

  it("announces the count, says the scope, and opens Evolution from Review", async () => {
    api.assistFlagged.mockResolvedValue([flaggedRun(), flaggedRun({ run_id: "run-2" })]);
    await renderTab({ filters: { ...FILTERS, range: "7d" } });
    const section = container.querySelector('section[aria-labelledby="flagged-heading"]');
    expect(section.querySelector('[role="status"]').textContent).toBe("2 flagged conversations");
    expect(section.textContent).toContain("last 7 days (UTC)");
    await click(section.querySelector("li button"));
    expect(onOpenTab).toHaveBeenCalledWith("evolution");
  });

  it("shows an error with Retry for the flagged list only, then recovers", async () => {
    api.assistFlagged.mockRejectedValueOnce(new Error("flagged down"));
    await renderTab();
    const section = container.querySelector('section[aria-labelledby="flagged-heading"]');
    expect(section.querySelector('[role="alert"]').textContent).toContain("flagged down");
    expect(kpi("judged")).toBeTruthy();
    api.assistFlagged.mockResolvedValue([flaggedRun()]);
    await click(buttonByText(section, "Retry"));
    expect(flaggedItems()).toHaveLength(1);
  });

  it("never shows question or answer text", async () => {
    api.assistFlagged.mockResolvedValue([flaggedRun({ questions: [{ question: "SECRET QUESTION", answer: "SECRET ANSWER" }] })]);
    await renderTab();
    expect(container.textContent).not.toContain("SECRET");
  });
});

describe("Evaluate now", () => {
  function button() {
    return buttonByText(container, "Evaluate now");
  }

  function message() {
    return container.querySelector("[data-evaluate-status]");
  }

  it("calls the API and says how many were queued and how many wait", async () => {
    api.assistEvaluateNow.mockResolvedValue({ queued: 2, waiting: 3 });
    await renderTab();
    expect(message().textContent).toBe("");
    await click(button());
    expect(api.assistEvaluateNow).toHaveBeenCalledTimes(1);
    expect(message().textContent).toBe("Queued 2; 3 waiting for the next hour");
    expect(message().getAttribute("role")).toBe("status");
  });

  it("says Nothing to evaluate when nothing was queued and nothing waits", async () => {
    api.assistEvaluateNow.mockResolvedValue({ queued: 0, waiting: 0 });
    await renderTab();
    await click(button());
    expect(message().textContent).toBe("Nothing to evaluate");
  });

  it("still reports the waiting ones when none could be queued", async () => {
    api.assistEvaluateNow.mockResolvedValue({ queued: 0, waiting: 4 });
    await renderTab();
    await click(button());
    expect(message().textContent).toBe("Queued 0; 4 waiting for the next hour");
  });

  it("is disabled while the call is in flight and cannot be sent twice", async () => {
    let resolve;
    api.assistEvaluateNow.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderTab();
    await click(button());
    expect(button().disabled).toBe(true);
    expect(message().textContent).toBe("Evaluating…");
    await click(button());
    expect(api.assistEvaluateNow).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ queued: 1, waiting: 0 }));
    expect(button().disabled).toBe(false);
    expect(message().textContent).toBe("Queued 1; 0 waiting for the next hour");
  });

  it("shows an error when the call fails, and can be tried again", async () => {
    api.assistEvaluateNow.mockRejectedValueOnce(new api.ApiError("Judge unavailable.", 502));
    await renderTab();
    await click(button());
    const alert = container.querySelector("[data-evaluate-error]");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(alert.textContent).toContain("Couldn't start the evaluation");
    expect(alert.textContent).toContain("Judge unavailable.");
    expect(button().disabled).toBe(false);
    api.assistEvaluateNow.mockResolvedValue({ queued: 1, waiting: 0 });
    await click(button());
    expect(container.querySelector("[data-evaluate-error]")).toBeNull();
    expect(message().textContent).toBe("Queued 1; 0 waiting for the next hour");
  });

  it("falls back to a generic message for an error without one", async () => {
    api.assistEvaluateNow.mockRejectedValue("nope");
    await renderTab();
    await click(button());
    expect(container.querySelector("[data-evaluate-error]").textContent).toContain("Something went wrong.");
  });

  it("is available even when there is no quality data yet", async () => {
    api.assistQuality.mockResolvedValue(emptyQuality());
    await renderTab();
    expect(button()).toBeTruthy();
  });
});

describe("states", () => {
  it("shows Loading while the numbers are on their way", async () => {
    api.assistQuality.mockReturnValue(new Promise(() => {}));
    await renderTab();
    expect(container.textContent).toContain("Loading…");
    expect(kpi("judged")).toBeNull();
    // The rest of the tab does not wait for it.
    expect(buttonByText(container, "Evaluate now")).toBeTruthy();
    expect(chip("All")).toBeTruthy();
  });

  it("shows Not enough data yet. for an empty database, well-formed and without NaN", async () => {
    api.assistQuality.mockResolvedValue(emptyQuality());
    api.assistTrends.mockResolvedValue({ bucket: "day", totals: {}, previous: null, points: [], version_markers: [] });
    await renderTab();
    expect(container.textContent).toContain("Not enough data yet.");
    expect(kpi("judged")).toBeNull();
    expect(container.textContent).not.toMatch(/NaN|undefined|Infinity/);
  });

  it("shows Couldn't load the quality numbers with Retry, and the rest still works", async () => {
    api.assistQuality.mockRejectedValueOnce(new Error("quality down"));
    await renderTab();
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Couldn't load the quality numbers");
    expect(alert.textContent).toContain("quality down");
    expect(buttonByText(container, "Evaluate now")).toBeTruthy();
    expect(chip("All")).toBeTruthy();
    api.assistQuality.mockResolvedValue(qualityFixture());
    await click(buttonByText(alert, "Retry"));
    expect(kpi("judged")).toBeTruthy();
  });

  it("keeps the quality-judge anchor in every state", async () => {
    api.assistQuality.mockRejectedValue(new Error("down"));
    await renderTab();
    expect(container.querySelector("#quality-judge")).toBeTruthy();
    await unmount();
    api.assistQuality.mockResolvedValue(emptyQuality());
    await renderTab();
    expect(container.querySelector("#quality-judge")).toBeTruthy();
    await unmount();
    api.assistQuality.mockResolvedValue(qualityFixture());
    await renderTab();
    const anchor = container.querySelector("#quality-judge");
    expect(anchor).toBeTruthy();
    expect(anchor.querySelector('[data-kpi="judged"]')).toBeTruthy();
    expect(anchor.textContent).toContain("Average judge grade over time");
    expect(buttonByText(anchor, "Evaluate now")).toBeTruthy();
  });

  it("shows Updating… on the numbers while a refetch is in flight, keeping the old ones", async () => {
    await renderTab();
    api.assistQuality.mockReturnValue(new Promise(() => {}));
    await rerender(createElement(QualityTab, props({ reloadKey: 1 })));
    expect(container.querySelector('[aria-busy="true"]')).toBeTruthy();
    expect(container.textContent).toContain("Updating…");
    expect(kpi("judged")).toBeTruthy();
  });
});

describe("insights brief", () => {
  it("has the insights brief panel once", async () => {
    await renderTab();
    expect(container.querySelectorAll("#insights-heading")).toHaveLength(1);
    api.assistInsights.mockResolvedValue({ brief: "A brief.", counts_used: { judged: 8 }, grounded: true, warnings: [] });
    await click(buttonByText(container, "Generate insights"));
    expect(container.querySelector('[data-testid="insights-brief"]').textContent).toBe("A brief.");
  });
});

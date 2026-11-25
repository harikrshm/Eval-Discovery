import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, assistOperations: vi.fn() };
});

import * as api from "../../api";
import OperationsTab from "./OperationsTab";
import { FILTERS, buttonByText, click, emptyOperations, mount, operationsFixture, rerender, settle, unmount } from "./testUtils";

// jsdom has no layout, so Recharts' ResponsiveContainer would measure 0 x 0 and draw nothing.
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

// Every ready test renders real Recharts charts; under a loaded full run that can pass the default 5 s.
vi.setConfig({ testTimeout: 30000 });

let container;
let originalObserver;

function props(overrides = {}) {
  return { filters: FILTERS, reloadKey: 0, ...overrides };
}

async function renderTab(overrides = {}) {
  container = await mount(createElement(OperationsTab, props(overrides)));
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

beforeEach(() => {
  vi.resetAllMocks();
  originalObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = SizedObserver;
  api.assistOperations.mockResolvedValue(operationsFixture());
});

afterEach(async () => {
  await unmount();
  globalThis.ResizeObserver = originalObserver;
});

describe("headline numbers", () => {
  it("shows p95 latency, the error rate, rate-limited calls and tokens", async () => {
    await renderTab();
    expect(kpi("p95").textContent).toContain("Latency p95");
    expect(kpi("p95").textContent).toContain("4.3 s");
    expect(kpi("error_rate").textContent).toContain("Error rate");
    expect(kpi("error_rate").textContent).toContain("16.7");
    expect(kpi("error_rate").textContent).toContain("%");
    // Rate-limited has its own number (a count, with its share of calls); it is not part of the error rate.
    expect(kpi("rate_limited").textContent).toContain("Rate-limited calls");
    expect(kpi("rate_limited").textContent).toContain("3");
    expect(kpi("rate_limited").textContent).toContain("25%");
    expect(kpi("tokens").textContent).toContain("Tokens in range");
    expect(kpi("tokens").textContent).toContain("12,700");
  });

  it("shows n/a for numbers that cannot be known, never 0", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({
        latency: [],
        outcomes: [],
        requests: [],
        totals: { calls: 0, error_rate: null, rate_limit_rate: null, p50_ms: null, p95_ms: null, tokens: 0 },
      })
    );
    await renderTab();
    expect(kpi("p95").textContent).toContain("n/a");
    expect(kpi("error_rate").textContent).toContain("n/a");
    expect(kpi("error_rate").textContent).not.toContain("0%");
    expect(kpi("rate_limited").textContent).toContain("n/a");
    expect(kpi("p95").textContent).not.toMatch(/\b0\b/);
  });

  it("formats milliseconds as seconds with one decimal above 1000", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({ totals: { ...operationsFixture().totals, p95_ms: 850 } })
    );
    await renderTab();
    expect(kpi("p95").textContent).toContain("850 ms");
    await rerender(createElement(OperationsTab, props({ reloadKey: 1 })));
    api.assistOperations.mockResolvedValue(
      operationsFixture({ totals: { ...operationsFixture().totals, p95_ms: 1049 } })
    );
    await rerender(createElement(OperationsTab, props({ reloadKey: 2 })));
    expect(kpi("p95").textContent).toContain("1.0 s");
  });

  it("says the numbers include the demo dataset unless the source is live", async () => {
    await renderTab();
    expect(container.querySelector("[data-demo-note]").textContent).toContain("includes demo dataset");
    await rerender(createElement(OperationsTab, props({ filters: { ...FILTERS, source: "demo" } })));
    expect(container.querySelector("[data-demo-note]").textContent).toContain("includes demo dataset");
    await rerender(createElement(OperationsTab, props({ filters: { ...FILTERS, source: "live" } })));
    expect(container.querySelector("[data-demo-note]")).toBeNull();
    expect(container.textContent).not.toContain("includes demo dataset");
  });

  it("explains what latency and the percentiles measure", async () => {
    await renderTab();
    const note = container.querySelector("[data-semantics]").textContent;
    expect(note).toContain("Latency is the total time a user waited, including retries");
    expect(note).toContain("Percentiles use chat calls only; counts and tokens include all call kinds");
  });
});

describe("latency", () => {
  it("lists both percentiles per bucket in UTC, with n/a for an empty bucket and ms formatting", async () => {
    await renderTab();
    expect(await tableOf("Latency over time")).toEqual([
      ["2026-11-24 10:37 UTC", "850 ms", "1.9 s", "5"],
      ["2026-11-24 11:37 UTC", "n/a", "n/a", "0"],
      ["2026-11-24 12:37 UTC", "1.2 s", "4.3 s", "4"],
    ]);
  });

  it("draws two lines with a gap for the empty bucket, and a text legend", async () => {
    await renderTab();
    const latency = card("Latency over time");
    expect(latency.querySelectorAll(".recharts-line-curve")).toHaveLength(2);
    // The two buckets with data are isolated by the gap: four dots in all, none at zero for the gap.
    expect(latency.querySelectorAll(".recharts-dot")).toHaveLength(4);
    const legend = latency.querySelector("[data-legend]").textContent;
    expect(legend).toContain("Median (p50)");
    expect(legend).toContain("Slowest 5% (p95)");
    expect(latency.textContent).toContain("UTC");
  });

  it("labels the hourly axis with real UTC times", async () => {
    await renderTab();
    const ticks = [...card("Latency over time").querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value")].map(
      (t) => t.textContent
    );
    expect(ticks).toEqual(["10:37", "11:37", "12:37"]);
  });

  it("has the DOM anchor ops-latency around the card, the same element before and after the data arrives", async () => {
    let resolve;
    api.assistOperations.mockReturnValue(new Promise((r) => (resolve = r)));
    await renderTab();
    const anchor = container.querySelector("#ops-latency");
    expect(anchor).not.toBeNull();
    expect(anchor.querySelector("h3").textContent).toBe("Latency over time");
    await act(async () => resolve(operationsFixture()));
    await settle();
    expect(container.querySelector("#ops-latency")).toBe(anchor);
    expect(anchor.querySelector("svg")).not.toBeNull();
  });

  it("says so, instead of an empty chart, when no chat call was timed", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({ latency: [{ t: "2026-11-24T10:37:00", p50_ms: null, p95_ms: null, n: 0 }] })
    );
    await renderTab();
    expect(card("Latency over time").textContent).toContain("No timed chat calls in this range yet.");
    expect(card("Latency over time").querySelector("table, svg")).toBeFalsy();
    expect(container.querySelector("#ops-latency").contains(card("Latency over time"))).toBe(true);
  });
});

describe("outcomes, requests and tokens", () => {
  it("lists every outcome per bucket by name, so they differ without colour", async () => {
    await renderTab();
    expect(await tableOf("Calls by outcome")).toEqual([
      ["2026-11-24 10:37 UTC", "5", "1", "0", "0"],
      ["2026-11-24 11:37 UTC", "0", "0", "0", "0"],
      ["2026-11-24 12:37 UTC", "2", "0", "3", "1"],
    ]);
    const outcomes = card("Calls by outcome");
    expect([...outcomes.querySelectorAll("thead th")].map((th) => th.textContent)).toEqual([
      "Time (UTC)",
      "OK",
      "Error",
      "Rate limited",
      "Timeout",
    ]);
    const legend = outcomes.querySelector("[data-legend]").textContent;
    for (const name of ["OK", "Error", "Rate limited", "Timeout"]) expect(legend).toContain(name);
    expect(outcomes.querySelector("[data-outcome-totals]").textContent).toBe(
      "In this range: OK 7, Error 1, Rate limited 3, Timeout 1."
    );
  });

  it("stacks the four outcome series, inside the ops-errors anchor", async () => {
    await renderTab();
    expect(card("Calls by outcome").querySelectorAll(".recharts-bar").length).toBe(4);
    expect(container.querySelector("#ops-errors").contains(card("Calls by outcome"))).toBe(true);
  });

  it("shows calls per bucket", async () => {
    await renderTab();
    expect(await tableOf("Calls over time")).toEqual([
      ["2026-11-24 10:37 UTC", "6"],
      ["2026-11-24 11:37 UTC", "0"],
      ["2026-11-24 12:37 UTC", "6"],
    ]);
  });

  it("shows tokens by kind of call", async () => {
    await renderTab();
    expect(await tableOf("Tokens by kind of call")).toEqual([
      ["Chat", "9,000"],
      ["Judge", "2,500"],
      ["Insights", "1,200"],
    ]);
  });

  it("names the day for day buckets and reads bounded timestamps as UTC", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({
        bucket: "day",
        requests: [{ t: "2026-11-23T10:37:00", n: 2 }],
        outcomes: [{ t: "2026-11-23T10:37:00", ok: 2, error: 0, rate_limited: 0, timeout: 0 }],
        latency: [{ t: "2026-11-23T10:37:00", p50_ms: 100, p95_ms: 200, n: 2 }],
      })
    );
    await renderTab();
    expect(await tableOf("Calls over time")).toEqual([["2026-11-23 10:37 UTC", "2"]]);
  });

  it("says so when no call was made, but keeps the outcome anchor", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({
        outcomes: [{ t: "2026-11-24T10:37:00", ok: 0, error: 0, rate_limited: 0, timeout: 0 }],
        requests: [{ t: "2026-11-24T10:37:00", n: 0 }],
        tokens_by_kind: { chat: 0, judge: 0, insights: 0 },
        totals: { calls: 0, error_rate: null, rate_limit_rate: null, p50_ms: null, p95_ms: null, tokens: 0 },
      })
    );
    await renderTab();
    expect(card("Calls by outcome").textContent).toContain("No calls in this range yet.");
    expect(container.querySelector("#ops-errors").contains(card("Calls by outcome"))).toBe(true);
    expect(card("Calls over time").textContent).toContain("No calls in this range yet.");
  });

  it("makes every chart a labelled image with a table equivalent, not an interactive chart", async () => {
    await renderTab();
    for (const title of ["Latency over time", "Calls by outcome", "Calls over time", "Tokens by kind of call"]) {
      expect(card(title).querySelector('[role="img"]'), title).toBeTruthy();
      expect(card(title).querySelector('[role="button"][tabindex]'), title).toBeNull();
    }
  });
});

describe("judge queue", () => {
  function queue() {
    return container.querySelector("[data-queue]");
  }

  it("shows what waits, what is queued, how old, and how much of the hourly cap was used", async () => {
    await renderTab();
    expect(queue().querySelector("h3").textContent).toBe("Judge queue");
    expect(queue().querySelector("[data-queue-summary]").textContent).toBe("3 waiting");
    expect(queue().querySelector('[data-queue-stat="queued_jobs"]').textContent).toContain("1");
    expect(queue().querySelector('[data-queue-stat="oldest"]').textContent).toContain("42 min");
    expect(queue().querySelector('[data-queue-stat="judged"]').textContent).toContain("7 of 20");
    expect(queue().querySelector('[data-queue-stat="unreadable"]').textContent).toContain("2");
  });

  it("explains the hourly cap and what unreadable means in plain words", async () => {
    await renderTab();
    const text = queue().querySelector("[data-queue-explanation]").textContent;
    expect(text).toContain("at most 20");
    expect(text).toContain("an hour");
    expect(text).toContain("unreadable");
  });

  it("says 0 waiting for an empty queue, with n/a for the age of nothing", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({ judge_queue: emptyOperations().judge_queue })
    );
    await renderTab();
    expect(queue().querySelector("[data-queue-summary]").textContent).toBe("0 waiting");
    expect(queue().querySelector('[data-queue-stat="oldest"]').textContent).toContain("n/a");
    expect(queue().querySelector('[data-queue-stat="judged"]').textContent).toContain("0 of 20");
  });

  it("reads hours when the oldest conversation has waited more than an hour", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({ judge_queue: { ...operationsFixture().judge_queue, oldest_waiting_minutes: 135 } })
    );
    await renderTab();
    expect(queue().querySelector('[data-queue-stat="oldest"]').textContent).toContain("2 h 15 min");
  });
});

describe("recent failed jobs", () => {
  function failed() {
    return container.querySelector("[data-failed-jobs]");
  }
  function rows() {
    return [...failed().querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((c) => c.textContent));
  }

  it("lists type, time in UTC and the fixed message", async () => {
    await renderTab();
    expect([...failed().querySelectorAll("thead th")].map((th) => th.textContent)).toEqual([
      "Type",
      "Time (UTC)",
      "Message",
    ]);
    expect(rows()).toEqual([
      ["Replay", "2026-11-24 11:00 UTC", "The judge could not read this conversation."],
      ["Judge a conversation", "2026-11-24 09:30 UTC", "No message recorded."],
    ]);
  });

  it("renders whatever arrives as plain text, never as HTML", async () => {
    api.assistOperations.mockResolvedValue(
      operationsFixture({
        recent_failed_jobs: [
          { id: "x", type: "<img src=x onerror=alert(1)>", created_at: "2026-11-24T11:00:00", error: "<script>alert(1)</script>" },
        ],
      })
    );
    await renderTab();
    expect(failed().querySelector("img, script")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(rows()).toEqual([["<img src=x onerror=alert(1)>", "2026-11-24 11:00 UTC", "<script>alert(1)</script>"]]);
  });

  it("says there are none when nothing failed", async () => {
    api.assistOperations.mockResolvedValue(operationsFixture({ recent_failed_jobs: [] }));
    await renderTab();
    expect(failed().textContent).toContain("No failed jobs.");
    expect(failed().querySelector("table")).toBeNull();
  });
});

describe("states", () => {
  const STATIC = ["#ops-latency", "#ops-errors"];

  it("shows Loading while the first answer is awaited, with the anchors and the explanations in place", async () => {
    api.assistOperations.mockReturnValue(new Promise(() => {}));
    await renderTab();
    expect(container.textContent).toContain("Loading…");
    for (const selector of STATIC) expect(container.querySelector(selector), selector).not.toBeNull();
    expect(container.querySelector("[data-semantics]")).not.toBeNull();
    expect(container.querySelector("[data-queue-explanation]")).not.toBeNull();
    expect(container.querySelector("[data-kpi]")).toBeNull();
  });

  it("shows Not enough data yet on an empty database, with the anchors and the explanations in place", async () => {
    api.assistOperations.mockResolvedValue(emptyOperations());
    await renderTab();
    expect(container.textContent).toContain("Not enough data yet.");
    for (const selector of STATIC) expect(container.querySelector(selector), selector).not.toBeNull();
    expect(container.querySelector("[data-semantics]")).not.toBeNull();
    expect(container.querySelector("[data-queue-explanation]")).not.toBeNull();
    expect(container.textContent).not.toMatch(/NaN|Infinity/);
    expect(container.querySelector("[data-kpi]")).toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });

  it("shows a failed request with its message and a Retry that asks again, anchors kept", async () => {
    api.assistOperations.mockRejectedValueOnce(new api.ApiError("Boom", 500));
    await renderTab();
    expect(container.textContent).toContain("Couldn't load the operations data: Boom");
    for (const selector of STATIC) expect(container.querySelector(selector), selector).not.toBeNull();
    expect(container.querySelector("[data-queue-explanation]")).not.toBeNull();
    await click(buttonByText(container, "Retry"));
    expect(api.assistOperations).toHaveBeenCalledTimes(2);
    expect(kpi("p95").textContent).toContain("4.3 s");
    expect(container.textContent).not.toContain("Couldn't load");
  });

  it("keeps the numbers on screen, marked as updating, while a refresh is in flight", async () => {
    await renderTab();
    api.assistOperations.mockReturnValue(new Promise(() => {}));
    await rerender(createElement(OperationsTab, props({ reloadKey: 1 })));
    expect(container.textContent).toContain("Updating…");
    expect(kpi("p95").textContent).toContain("4.3 s");
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
  });
});

describe("requests", () => {
  it("asks for the operations numbers with the console filters", async () => {
    const filters = { range: "7d", source: "live", version: 2 };
    await renderTab({ filters });
    expect(api.assistOperations).toHaveBeenCalledTimes(1);
    expect(api.assistOperations).toHaveBeenCalledWith(filters);
  });

  it("refetches when the filters or the reload key change", async () => {
    await renderTab();
    await rerender(createElement(OperationsTab, props({ filters: { ...FILTERS, range: "24h" } })));
    expect(api.assistOperations).toHaveBeenLastCalledWith({ ...FILTERS, range: "24h" });
    const before = api.assistOperations.mock.calls.length;
    await rerender(createElement(OperationsTab, props({ filters: { ...FILTERS, range: "24h" }, reloadKey: 1 })));
    expect(api.assistOperations.mock.calls.length).toBe(before + 1);
  });
});

describe("layout", () => {
  it("lets the headline cards shrink in the grid", async () => {
    await renderTab();
    expect(container.querySelector('[data-kpi="p95"]').className).toContain("min-w-0");
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, assistOverview: vi.fn() };
});

import * as api from "../../api";
import OverviewTab from "./OverviewTab";
import { FILTERS, buttonByText, click, emptyOverview, mount, overviewFixture, unmount } from "./testUtils";

let container;

function trendCard() {
  return [...container.querySelectorAll("section")].find((s) => s.querySelector("h3")?.textContent === "Quality over time");
}

async function renderOverview(props = {}) {
  const onOpenTab = vi.fn();
  container = await mount(createElement(OverviewTab, { filters: FILTERS, reloadKey: 0, onOpenTab, ...props }));
  return { onOpenTab };
}

beforeEach(() => {
  vi.resetAllMocks();
  api.assistOverview.mockResolvedValue(overviewFixture());
});

afterEach(unmount);

describe("OverviewTab", () => {
  it("fetches the overview with the filters it was given", async () => {
    await renderOverview({ filters: { range: "7d", source: "live", version: 2 } });
    expect(api.assistOverview).toHaveBeenCalledWith({ range: "7d", source: "live", version: 2 });
  });

  it("shows loading first", async () => {
    api.assistOverview.mockReturnValue(new Promise(() => {}));
    await renderOverview();
    expect(container.querySelector('[role="status"]').textContent).toBe("Loading…");
  });

  it("renders the four KPI values with their change in words", async () => {
    await renderOverview();
    const cards = [...container.querySelectorAll("[data-kpi]")];
    expect(cards.map((c) => c.getAttribute("data-kpi"))).toEqual(["questions", "ok_share", "open_flagged", "live_version"]);
    expect(cards[0].textContent).toContain("Questions");
    expect(cards[0].textContent).toContain("120");
    expect(cards[0].textContent).toContain("up 12.5 % vs previous period");
    expect(cards[0].textContent).toContain("(better)");
    expect(cards[1].textContent).toContain("Auto-judged OK");
    expect(cards[1].textContent).toContain("75");
    expect(cards[1].textContent).toContain("%");
    expect(cards[1].textContent).toContain("down 2.5 pts vs previous period");
    expect(cards[1].textContent).toContain("(worse)");
    expect(cards[2].textContent).toContain("Open flagged conversations");
    expect(cards[2].textContent).toContain("4");
    expect(cards[2].querySelector("[data-delta]")).toBeNull();
    expect(cards[3].textContent).toContain("v2");
  });

  it("shows n/a, not 0%, for a share with nothing judged", async () => {
    const payload = overviewFixture();
    payload.kpis[1] = { ...payload.kpis[1], value: null, delta: null };
    api.assistOverview.mockResolvedValue(payload);
    await renderOverview();
    const card = container.querySelector('[data-kpi="ok_share"]');
    expect(card.textContent).toContain("n/a");
    expect(card.textContent).not.toContain("0%");
  });

  it("renders no alert strip when there are no alerts", async () => {
    await renderOverview();
    expect(container.querySelector("[data-alert-strip]")).toBeNull();
  });

  it("renders the active alerts, and a click opens the alert's tab and anchor", async () => {
    api.assistOverview.mockResolvedValue(
      overviewFixture({
        alerts: [
          { id: "error_rate", severity: "critical", title: "Error rate is high", detail: "12% of calls failed", tab: "operations", anchor: "ops-errors", demo: false },
        ],
      })
    );
    const { onOpenTab } = await renderOverview();
    const alert = [...container.querySelectorAll("[data-alert-strip] button")][0];
    expect(alert.textContent).toContain("Error rate is high");
    await click(alert);
    expect(onOpenTab).toHaveBeenCalledWith("operations", "ops-errors");
  });

  it("draws the quality trend with a text table, version markers and UTC time labels", async () => {
    await renderOverview();
    const card = trendCard();
    expect(card.textContent).toContain("Quality over time");
    expect(card.textContent).toContain("UTC");
    await click(buttonByText(card, "View as table"));
    const rows = [...card.querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((c) => c.textContent));
    expect(rows).toEqual([
      ["2026-11-22 10:00 UTC", "3.5"],
      ["2026-11-23 10:00 UTC", "n/a"],
      ["2026-11-24 10:00 UTC", "4.25"],
    ]);
    expect(card.querySelector("[data-legend]").textContent).toContain("v2");
  });

  it("marks a demo version marker as demo", async () => {
    api.assistOverview.mockResolvedValue(
      overviewFixture({ version_markers: [{ at: "2026-11-23T09:00:00", version: 1, source: "demo", label: "v1 (demo)" }] })
    );
    await renderOverview();
    const legend = container.querySelector("[data-legend]").textContent;
    expect(legend).toContain("v1 (demo)");
    expect(legend).not.toContain("(demo) (demo)");
  });

  it("says there is no judged data yet when the trend has no points", async () => {
    api.assistOverview.mockResolvedValue(overviewFixture({ quality_trend: [], version_markers: [{ at: "2026-11-23T09:00:00", version: 2, source: "live", label: "v2" }] }));
    await renderOverview();
    expect(container.textContent).toContain("Quality over time");
    expect(container.textContent).toContain("No judged conversations in this range yet.");
    expect(container.querySelector("table")).toBeNull();
  });

  it("shows the improvement loop with one current step and its note", async () => {
    await renderOverview();
    const steps = container.querySelectorAll('ol li[aria-current="step"]');
    expect(steps).toHaveLength(1);
    expect(steps[0].textContent).toContain("Criterion chosen");
    expect(container.textContent).toContain("The loop runs on live conversations only.");
  });

  it("opens the Evolution tab from the loop", async () => {
    const { onOpenTab } = await renderOverview();
    await click(buttonByText(container, "Open Evolution"));
    expect(onOpenTab).toHaveBeenCalledWith("evolution");
  });

  it("says 'Not enough data yet.' for an empty payload", async () => {
    api.assistOverview.mockResolvedValue(emptyOverview());
    await renderOverview();
    expect(container.textContent).toContain("Not enough data yet.");
    expect(container.querySelector("[data-kpi]")).toBeNull();
    expect(container.textContent).not.toContain("NaN");
  });

  it("shows a load error with Retry that fetches again", async () => {
    api.assistOverview.mockRejectedValueOnce(new api.ApiError("Boom", 500));
    await renderOverview();
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Couldn't load the overview");
    expect(alert.textContent).toContain("Boom");
    await click(buttonByText(container, "Retry"));
    expect(api.assistOverview).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector('[data-kpi="questions"]')).not.toBeNull();
  });

  it("refetches when the filters or the reload key change", async () => {
    await renderOverview();
    await unmount();
    await renderOverview({ reloadKey: 3 });
    expect(api.assistOverview).toHaveBeenCalledTimes(2);
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import ReplayChart from "./ReplayChart";
import { buttonByText, click, mount, unmount } from "./testUtils";

// Real Recharts, drawn at a fixed size (jsdom has no layout); slow under a loaded full run.
vi.setConfig({ testTimeout: 30000 });

let container;

afterEach(async () => {
  await unmount();
});

const ROWS = [
  { number: 2, label: "v2", before: 1, after: 3, total: 4 },
  { number: 4, label: "v4", before: 2, after: null, total: 3 },
];

describe("ReplayChart", () => {
  it("draws grouped bars, passed before and after, for each version", async () => {
    container = await mount(createElement(ReplayChart, { rows: ROWS, width: 640 }));
    expect(container.querySelector("h3").textContent).toBe("Before and after replay");
    // v2: two bars; v4: only the "before" bar (no after result, so no bar, not a zero).
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(3);
    const legend = container.querySelector("[data-legend]").textContent;
    expect(legend).toContain("Passed before");
    expect(legend).toContain("Passed after");
  });

  it("has a real table with the counts, n/a for a side with no result", async () => {
    container = await mount(createElement(ReplayChart, { rows: ROWS, width: 640 }));
    await click(buttonByText(container, "View as table"));
    const cells = [...container.querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((c) => c.textContent));
    expect(cells).toEqual([
      ["v2", "1", "3", "4"],
      ["v4", "2", "n/a", "3"],
    ]);
    const heads = [...container.querySelectorAll("thead th")].map((th) => th.textContent);
    expect(heads).toEqual(["Version", "Passed before", "Passed after", "Tests checked"]);
  });

  it("says in words that the counts are replay results of the version's required tests", async () => {
    container = await mount(createElement(ReplayChart, { rows: ROWS, width: 640 }));
    expect(container.textContent).toContain("required tests");
  });
});

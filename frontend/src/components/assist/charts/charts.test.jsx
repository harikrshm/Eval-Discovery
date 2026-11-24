// Chart kit tests (react-dom + act, no testing library).
//
// jsdom has no layout, so Recharts' ResponsiveContainer measures 0 x 0 and draws nothing. Decision:
// TrendChart and BarsChart take an optional `width` prop; when it is set they draw at that fixed
// size and skip ResponsiveContainer. Production callers omit it (responsive, 100% wide); these
// tests pass it. Nothing is mocked, so the real Recharts output (paths, dots, labels) is asserted.
import { afterEach, describe, expect, it, vi } from "vitest";
import { buttonByText, click, mount, pressKey, unmount } from "../testUtils";
import BarsChart from "./BarsChart";
import ChartCard from "./ChartCard";
import KpiCard from "./KpiCard";
import PanelState from "./PanelState";
import TrendChart from "./TrendChart";
import { MAX_SERIES, SERIES, STATUS, seriesColor } from "./palette";

afterEach(unmount);

const HOURS = ["10", "11", "12", "13", "14"].map((h) => `2026-11-20T${h}:00:00`);

function hourly(aValues, bValues) {
  return HOURS.map((at, i) => ({ at, a: aValues[i], b: bValues[i] }));
}

const SERIES_AB = [
  { key: "a", label: "Judge grade" },
  { key: "b", label: "Thumbs-up rate" },
];

function tickTexts(container) {
  return [...container.querySelectorAll(".recharts-xAxis-tick-labels .recharts-cartesian-axis-tick-value")].map((t) =>
    t.textContent.trim(),
  );
}

describe("palette", () => {
  it("has at most six series colours, all distinct, and status colours apart from them", () => {
    expect(SERIES.length).toBeLessThanOrEqual(6);
    expect(MAX_SERIES).toBe(SERIES.length);
    expect(new Set(SERIES).size).toBe(SERIES.length);
    for (const colour of Object.values(STATUS)) expect(SERIES).not.toContain(colour);
  });

  it("assigns by position, honours an override and never cycles past the last slot", () => {
    expect(seriesColor(0)).toBe(SERIES[0]);
    expect(seriesColor(1, "#123456")).toBe("#123456");
    expect(SERIES).not.toContain(seriesColor(MAX_SERIES));
  });
});

describe("PanelState", () => {
  it("shows a status message while loading", async () => {
    const container = await mount(
      <PanelState state="loading">
        <p>content</p>
      </PanelState>,
    );
    const status = container.querySelector('[role="status"]');
    expect(status.textContent).toBe("Loading…");
    expect(container.textContent).not.toContain("content");
  });

  it("shows the empty text", async () => {
    const container = await mount(
      <PanelState state="empty">
        <p>content</p>
      </PanelState>,
    );
    expect(container.textContent).toBe("Not enough data yet.");
  });

  it("shows an alert with what failed and why, and Retry calls onRetry", async () => {
    const onRetry = vi.fn();
    const container = await mount(
      <PanelState state="error" what="quality trends" error="HTTP 500" onRetry={onRetry}>
        <p>content</p>
      </PanelState>,
    );
    expect(container.querySelector('[role="alert"]').textContent).toContain("Couldn't load quality trends: HTTP 500");
    await click(buttonByText(container, "Retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(container.textContent).not.toContain("content");
  });

  it("shows no Retry button when there is nothing to retry", async () => {
    const container = await mount(<PanelState state="error" error="boom" />);
    expect(buttonByText(container, "Retry")).toBeUndefined();
  });

  it("renders its children when ready", async () => {
    const container = await mount(
      <PanelState state="ready">
        <p>content</p>
      </PanelState>,
    );
    expect(container.textContent).toBe("content");
    expect(container.querySelector('[role="status"],[role="alert"]')).toBeNull();
  });
});

describe("ChartCard", () => {
  const COLUMNS = ["Hour", "Grade", "Rate"];
  const ROWS = [
    ["10:00", 4.2, null],
    ["11:00", null, 0.5],
    ["12:00", 4.4, "55%"],
  ];

  async function card(extra = {}) {
    return mount(
      <ChartCard title="Quality" subtitle="Last 24 hours" tableColumns={COLUMNS} tableRows={ROWS} {...extra}>
        <svg data-testid="chart" />
      </ChartCard>,
    );
  }

  it("shows the heading, subtitle and chart, with the chart wrapper described for screen readers", async () => {
    const container = await card();
    expect(container.querySelector("h3").textContent).toBe("Quality");
    expect(container.textContent).toContain("Last 24 hours");
    const wrapper = container.querySelector('[role="img"]');
    expect(wrapper.querySelector("svg")).not.toBeNull();
    const label = wrapper.getAttribute("aria-label");
    expect(label).toContain("Quality");
    expect(label).toContain("10:00");
    expect(label).toContain("12:00");
    expect(label).toContain("Rate 55%");
    expect(container.querySelector("table")).toBeNull();
  });

  it("toggles a real table whose rows equal tableRows, with n/a for null", async () => {
    const container = await card();
    const toggle = buttonByText(container, "View as table");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await click(toggle);

    const table = container.querySelector("table");
    expect(table.querySelector("caption").textContent).toContain("Quality");
    expect([...table.querySelectorAll("thead th")].map((th) => th.textContent)).toEqual(COLUMNS);
    const body = [...table.querySelectorAll("tbody tr")].map((tr) => [...tr.children].map((td) => td.textContent));
    expect(body).toEqual([
      ["10:00", "4.2", "n/a"],
      ["11:00", "n/a", "0.5"],
      ["12:00", "4.4", "55%"],
    ]);
    expect(container.querySelector('[role="img"] svg')).not.toBeNull();

    await click(buttonByText(container, "Hide table"));
    expect(container.querySelector("table")).toBeNull();
  });

  it("says so in the label when there is no data and when the chart holds focusable bars", async () => {
    const empty = await mount(
      <ChartCard title="Quality" tableColumns={COLUMNS} tableRows={[]}>
        <svg />
      </ChartCard>,
    );
    expect(empty.querySelector('[role="img"]').getAttribute("aria-label")).toContain("no data");
    await unmount();

    const interactive = await card({ interactive: true });
    expect(interactive.querySelector('[role="img"]')).toBeNull();
    expect(interactive.querySelector('[role="group"]').getAttribute("aria-label")).toContain("Quality");
  });
});

describe("KpiCard", () => {
  it("shows label, value and unit, and n/a for a missing value", async () => {
    const container = await mount(<KpiCard label="Judge grade" value={4.2} unit="/ 5" />);
    expect(container.textContent).toContain("Judge grade");
    expect(container.textContent).toContain("4.2");
    expect(container.textContent).toContain("/ 5");
    await unmount();

    const missing = await mount(<KpiCard label="Judge grade" value={null} unit="/ 5" delta={2} />);
    expect(missing.textContent).toContain("n/a");
    expect(missing.textContent).not.toContain("/ 5");
    expect(missing.textContent).not.toContain("up 2");
  });

  it("states a positive delta in words with an arrow, and judges it by words too", async () => {
    const container = await mount(<KpiCard label="Thumbs-up" value="61%" delta={3} deltaGood="up" />);
    const delta = container.querySelector("[data-delta]");
    expect(delta.textContent).toContain("up 3 pts");
    expect(delta.textContent).toContain("better");
    expect(delta.querySelector("svg").getAttribute("aria-hidden")).toBe("true");
    expect(delta.dataset.delta).toBe("up");
  });

  it("uses a different word and arrow for a negative delta, and worse when down is bad", async () => {
    const container = await mount(<KpiCard label="Thumbs-up" value="58%" delta={-2.5} deltaGood="up" />);
    const delta = container.querySelector("[data-delta]");
    expect(delta.textContent).toContain("down 2.5 pts");
    expect(delta.textContent).toContain("worse");
    expect(delta.dataset.delta).toBe("down");
  });

  it("says no change for zero and omits judging when deltaGood is not given", async () => {
    const zero = await mount(<KpiCard label="x" value={1} delta={0} deltaGood="up" />);
    expect(zero.querySelector("[data-delta]").textContent).toContain("no change");
    await unmount();

    const plain = await mount(<KpiCard label="x" value={1} delta={4} />);
    const text = plain.querySelector("[data-delta]").textContent;
    expect(text).toContain("up 4 pts");
    expect(text).not.toMatch(/better|worse/);
  });

  it("shows no delta line when the delta is missing", async () => {
    const container = await mount(<KpiCard label="x" value={1} delta={null} />);
    expect(container.querySelector("[data-delta]")).toBeNull();
  });

  it("draws a decorative sparkline with gaps for null points", async () => {
    const container = await mount(<KpiCard label="x" value={1} sparkline={[1, 2, null, 3, 4]} />);
    const spark = container.querySelector('[data-sparkline]');
    expect(spark.getAttribute("aria-hidden")).toBe("true");
    const path = spark.querySelector(".recharts-line-curve");
    expect(path.getAttribute("d").match(/M/g)).toHaveLength(2);
  });

  it("draws no sparkline when every point is missing", async () => {
    const container = await mount(<KpiCard label="x" value={1} sparkline={[null, null]} />);
    expect(container.querySelector("[data-sparkline]")).toBeNull();
  });
});

describe("TrendChart", () => {
  const WIDTH = 600;

  it("draws one line per series and a gap, not a zero, where a value is null", async () => {
    const data = hourly([4, 4.2, null, 4.4, 4.5], [0.5, 0.52, 0.54, 0.55, 0.56]);
    const container = await mount(
      <TrendChart data={data} xKey="at" series={SERIES_AB} bucket="hour" width={WIDTH} height={200} />,
    );
    const paths = [...container.querySelectorAll(".recharts-line-curve")];
    expect(paths).toHaveLength(2);
    const segments = paths.map((p) => p.getAttribute("d").match(/M/g).length);
    expect(segments).toEqual([2, 1]);
    // 4 observed points on the gappy series, 5 on the full one; the null has no dot.
    const dots = [...container.querySelectorAll(".recharts-line-dots")].map(
      (layer) => layer.querySelectorAll(".recharts-dot").length,
    );
    expect(dots).toEqual([4, 5]);
  });

  it("uses one palette colour per series by position and honours a pinned colour", async () => {
    const data = hourly([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]);
    const container = await mount(
      <TrendChart
        data={data}
        xKey="at"
        series={[SERIES_AB[0], { ...SERIES_AB[1], color: "#123456" }]}
        bucket="hour"
        width={WIDTH}
      />,
    );
    const strokes = [...container.querySelectorAll(".recharts-line-curve")].map((p) => p.getAttribute("stroke"));
    expect(strokes).toEqual([SERIES[0], "#123456"]);
  });

  it("shows the legend as text, always", async () => {
    const container = await mount(
      <TrendChart data={hourly([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])} xKey="at" series={SERIES_AB} bucket="hour" width={WIDTH} />,
    );
    const legend = container.querySelector('[data-legend]');
    expect(legend.textContent).toContain("Judge grade");
    expect(legend.textContent).toContain("Thumbs-up rate");
  });

  it("formats ISO timestamps as hour of day for hourly data (UTC, whatever the machine zone)", async () => {
    const container = await mount(
      <TrendChart data={hourly([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])} xKey="at" series={SERIES_AB} bucket="hour" width={WIDTH} />,
    );
    const ticks = tickTexts(container);
    expect(ticks.length).toBeGreaterThan(1);
    for (const tick of ticks) expect(tick).toMatch(/^\d\d:00$/);
    expect(ticks).toContain("10:00");
  });

  it("formats ISO timestamps as short dates for daily data, with or without a Z suffix", async () => {
    const days = ["2026-11-18T09:30:00Z", "2026-11-19T09:30:00Z", "2026-11-20T09:30:00Z"];
    const data = days.map((at, i) => ({ at, a: i, b: i }));
    const container = await mount(
      <TrendChart data={data} xKey="at" series={SERIES_AB} bucket="day" width={WIDTH} />,
    );
    const ticks = tickTexts(container);
    expect(ticks).toContain("Nov 18");
    expect(ticks).toContain("Nov 20");
    expect(ticks.join(" ")).not.toContain("09:30");
  });

  it("draws a dashed vertical line with a text label per marker, and demo markers say demo", async () => {
    const container = await mount(
      <TrendChart
        data={hourly([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])}
        xKey="at"
        series={SERIES_AB}
        bucket="hour"
        width={WIDTH}
        markers={[
          { at: "2026-11-20T11:30:00", label: "Prompt v3" },
          { at: "2026-11-20T13:00:00", label: "Seed", demo: true },
        ]}
      />,
    );
    const lines = [...container.querySelectorAll(".recharts-reference-line")];
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(line.querySelector("line").getAttribute("stroke-dasharray")).toBeTruthy();
    }
    // Recharts draws the label text in a separate layer from the line, so read all marker labels.
    const labels = [...container.querySelectorAll("text.recharts-label")].map((t) => t.textContent);
    expect(labels).toEqual(["Prompt v3", "Seed (demo)"]);
    // The same words are in the plain-text legend, which does not depend on where the SVG puts them.
    expect(container.querySelector("[data-legend]").textContent.toLowerCase()).toContain("seed (demo)");
  });

  it("is keyboard focusable through the library's accessibility layer", async () => {
    const container = await mount(
      <TrendChart data={hourly([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])} xKey="at" series={SERIES_AB} bucket="hour" width={WIDTH} />,
    );
    expect(container.querySelector("svg.recharts-surface").getAttribute("tabindex")).toBe("0");
  });

  it("renders empty data and all-null series without throwing", async () => {
    const empty = await mount(<TrendChart data={[]} xKey="at" series={SERIES_AB} bucket="day" width={WIDTH} />);
    expect(empty.querySelector("svg")).not.toBeNull();
    await unmount();

    const nulls = await mount(
      <TrendChart data={hourly([null, null, null, null, null], [null, null, null, null, null])} xKey="at" series={SERIES_AB} bucket="hour" width={WIDTH} />,
    );
    expect(nulls.querySelectorAll(".recharts-dot")).toHaveLength(0);
  });

  it("ignores rows whose timestamp cannot be read instead of crashing", async () => {
    const data = [...hourly([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]), { at: "not a date", a: 9, b: 9 }];
    const container = await mount(
      <TrendChart data={data} xKey="at" series={SERIES_AB} bucket="hour" width={WIDTH} />,
    );
    expect(container.querySelectorAll(".recharts-line-dots")[0].querySelectorAll(".recharts-dot")).toHaveLength(5);
  });
});

describe("BarsChart", () => {
  const DATA = [
    { intent: "claim_explain", rate: 0.5 },
    { intent: "navigation_help", rate: 0.25 },
    { intent: "other", rate: null },
  ];
  const SERIES_RATE = [{ key: "rate", label: "Thumbs-up rate" }];
  const pct = (v) => `${Math.round(v * 100)}%`;

  it("draws one bar per observed value with a value label, and none for null", async () => {
    const container = await mount(
      <BarsChart data={DATA} categoryKey="intent" series={SERIES_RATE} valueFormat={pct} width={500} />,
    );
    expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(2);
    const labels = [...container.querySelectorAll(".recharts-label")].map((l) => l.textContent);
    expect(labels).toEqual(expect.arrayContaining(["50%", "25%"]));
    expect(container.textContent).toContain("other (n/a)");
  });

  it("makes bars buttons only when onSelect is given", async () => {
    const plain = await mount(
      <BarsChart data={DATA} categoryKey="intent" series={SERIES_RATE} width={500} />,
    );
    expect(plain.querySelectorAll('[role="button"]')).toHaveLength(0);
  });

  it("calls onSelect with the category on click, Enter and Space, and not on other keys", async () => {
    const onSelect = vi.fn();
    const container = await mount(
      <BarsChart data={DATA} categoryKey="intent" series={SERIES_RATE} width={500} onSelect={onSelect} />,
    );
    const bars = [...container.querySelectorAll('[role="button"]')];
    expect(bars).toHaveLength(2);
    expect(bars[0].getAttribute("tabindex")).toBe("0");
    expect(bars[0].getAttribute("aria-label")).toContain("claim_explain");

    await click(bars[0]);
    expect(onSelect).toHaveBeenLastCalledWith("claim_explain");

    await pressKey(bars[1], "Enter");
    expect(onSelect).toHaveBeenLastCalledWith("navigation_help");

    await pressKey(bars[0], " ");
    expect(onSelect).toHaveBeenLastCalledWith("claim_explain");

    onSelect.mockClear();
    await pressKey(bars[0], "a");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("supports vertical columns, grouped series and stacked series", async () => {
    const data = [
      { section: "claims", live: 3, demo: 2 },
      { section: "family", live: 1, demo: 4 },
    ];
    const series = [
      { key: "live", label: "Live" },
      { key: "demo", label: "Demo" },
    ];
    for (const props of [{ layout: "vertical" }, { grouped: true }, { grouped: false }]) {
      const container = await mount(
        <BarsChart data={data} categoryKey="section" series={series} width={500} {...props} />,
      );
      expect(container.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(4);
      expect(container.querySelector("[data-legend]").textContent).toContain("Live");
      await unmount();
    }
  });

  it("renders empty data without throwing", async () => {
    const container = await mount(
      <BarsChart data={[]} categoryKey="intent" series={SERIES_RATE} width={500} onSelect={() => {}} />,
    );
    expect(container.querySelectorAll('[role="button"]')).toHaveLength(0);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import ConsoleControls from "./ConsoleControls";
import { DEFAULT_FILTERS, sourceBadgeText } from "./consoleFilters";
import { buttonByText, click, mount, unmount } from "./testUtils";

let container;

async function renderControls(props = {}) {
  const onChange = vi.fn();
  const onRefresh = vi.fn();
  container = await mount(
    createElement(ConsoleControls, {
      value: DEFAULT_FILTERS,
      onChange,
      versions: [1, 2, 3],
      onRefresh,
      loading: false,
      ...props,
    })
  );
  return { onChange, onRefresh };
}

async function choose(select, value) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

afterEach(unmount);

describe("ConsoleControls", () => {
  it("defaults to 30 days, all sources and no version", () => {
    expect(DEFAULT_FILTERS).toEqual({ range: "30d", source: "all", version: null });
  });

  it("offers the four ranges as a group of pressed-state buttons", async () => {
    await renderControls();
    const group = container.querySelector('[role="group"][aria-label="Time range"]');
    const buttons = [...group.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["24 hours", "7 days", "30 days", "All"]);
    expect(buttons.map((b) => b.getAttribute("aria-pressed"))).toEqual(["false", "false", "true", "false"]);
  });

  it("calls onChange with the full filters object when the range changes", async () => {
    const { onChange } = await renderControls({ value: { range: "30d", source: "live", version: 2 } });
    await click(buttonByText(container, "7 days"));
    expect(onChange).toHaveBeenCalledWith({ range: "7d", source: "live", version: 2 });
    await click(buttonByText(container, "24 hours"));
    expect(onChange).toHaveBeenLastCalledWith({ range: "24h", source: "live", version: 2 });
    await click(buttonByText(container, "All"));
    expect(onChange).toHaveBeenLastCalledWith({ range: "all", source: "live", version: 2 });
  });

  it("changes the source from a labelled select", async () => {
    const { onChange } = await renderControls();
    const select = container.querySelector("select#console-source");
    expect(container.querySelector('label[for="console-source"]').textContent).toBe("Source");
    expect([...select.options].map((o) => [o.value, o.textContent])).toEqual([
      ["all", "All"],
      ["live", "Live only"],
      ["demo", "Demo dataset"],
    ]);
    await choose(select, "demo");
    expect(onChange).toHaveBeenCalledWith({ range: "30d", source: "demo", version: null });
  });

  it("changes the version, with All plus the versions given, and null for All", async () => {
    const { onChange } = await renderControls({ value: { range: "30d", source: "all", version: 2 }, versions: [1, 2, 3] });
    const select = container.querySelector("select#console-version");
    expect(container.querySelector('label[for="console-version"]').textContent).toBe("Version");
    expect([...select.options].map((o) => o.textContent)).toEqual(["All", "v1", "v2", "v3"]);
    expect(select.value).toBe("2");
    await choose(select, "3");
    expect(onChange).toHaveBeenLastCalledWith({ range: "30d", source: "all", version: 3 });
    await choose(select, "");
    expect(onChange).toHaveBeenLastCalledWith({ range: "30d", source: "all", version: null });
  });

  it("keeps a selected version in the list even before the versions arrive", async () => {
    await renderControls({ value: { range: "30d", source: "all", version: 4 }, versions: [] });
    const select = container.querySelector("select#console-version");
    expect([...select.options].map((o) => o.textContent)).toEqual(["All", "v4"]);
    expect(select.value).toBe("4");
  });

  it("refreshes, and disables Refresh while loading", async () => {
    const { onRefresh } = await renderControls();
    await click(buttonByText(container, "Refresh data"));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await unmount();
    await renderControls({ loading: true });
    expect(buttonByText(container, "Refresh data").disabled).toBe(true);
  });

  it("always shows the source badge, worded for each source", async () => {
    expect(sourceBadgeText("all")).toBe("Data: Live + demo dataset");
    expect(sourceBadgeText("live")).toBe("Data: Live only");
    expect(sourceBadgeText("demo")).toBe("Data: Demo dataset");
    for (const [source, text] of [
      ["all", "Data: Live + demo dataset"],
      ["live", "Data: Live only"],
      ["demo", "Data: Demo dataset"],
    ]) {
      await renderControls({ value: { range: "30d", source, version: null } });
      expect(container.querySelector("[data-source-badge]").textContent).toBe(text);
      await unmount();
    }
  });

  it("says that times are UTC", async () => {
    await renderControls();
    expect(container.textContent).toContain("UTC");
  });

  it("wraps on a narrow window instead of using fixed widths", async () => {
    await renderControls();
    expect(container.firstChild.className).toContain("flex-wrap");
    expect(container.innerHTML).not.toMatch(/style="[^"]*width:\s*\d+px/);
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import AlertStrip from "./AlertStrip";
import LoopStepper from "./LoopStepper";
import { click, mount, unmount } from "./testUtils";

let container;

afterEach(unmount);

const ALERTS = [
  {
    id: "error_rate",
    severity: "critical",
    title: "Error rate is high",
    detail: "12% of calls failed",
    tab: "operations",
    anchor: "ops-errors",
    demo: false,
  },
  {
    id: "reask_rise",
    severity: "warning",
    title: "Re-asks are rising",
    detail: "Up 6 points",
    tab: "adoption",
    anchor: "adoption-reasks",
    demo: true,
  },
];

describe("AlertStrip", () => {
  it("renders nothing for no alerts", async () => {
    container = await mount(createElement(AlertStrip, { alerts: [], onOpen: vi.fn() }));
    expect(container.innerHTML).toBe("");
    await unmount();
    container = await mount(createElement(AlertStrip, { alerts: undefined, onOpen: vi.fn() }));
    expect(container.innerHTML).toBe("");
  });

  it("renders each alert as a button with its severity in words, and a demo tag only on demo alerts", async () => {
    container = await mount(createElement(AlertStrip, { alerts: ALERTS, onOpen: vi.fn() }));
    const buttons = [...container.querySelectorAll("button")];
    expect(buttons).toHaveLength(2);
    expect(buttons[0].textContent).toContain("Critical");
    expect(buttons[0].textContent).toContain("Error rate is high");
    expect(buttons[0].textContent).toContain("12% of calls failed");
    expect(buttons[0].textContent).not.toContain("demo");
    expect(buttons[1].textContent).toContain("Warning");
    expect(buttons[1].textContent).toContain("demo");
    expect(container.querySelectorAll("[data-demo-tag]")).toHaveLength(1);
  });

  it("calls onOpen with the alert when clicked", async () => {
    const onOpen = vi.fn();
    container = await mount(createElement(AlertStrip, { alerts: ALERTS, onOpen }));
    await click(container.querySelectorAll("button")[1]);
    expect(onOpen).toHaveBeenCalledWith(ALERTS[1]);
  });
});

describe("LoopStepper", () => {
  const STEPS = [
    { id: "flagged", label: "Flagged", state: "done", detail: "4 flagged" },
    { id: "pattern", label: "Pattern", state: "current", detail: null },
    { id: "criterion", label: "Criterion", state: "todo", detail: null },
  ];

  it("is an ordered list with the state in text and exactly one current step", async () => {
    container = await mount(createElement(LoopStepper, { steps: STEPS }));
    const list = container.querySelector("ol");
    const items = [...list.querySelectorAll("li")];
    expect(items.map((li) => li.getAttribute("aria-current"))).toEqual([null, "step", null]);
    expect(container.querySelectorAll('[aria-current="step"]')).toHaveLength(1);
    expect(items[0].textContent).toContain("Flagged");
    expect(items[0].textContent).toContain("Done");
    expect(items[0].textContent).toContain("4 flagged");
    expect(items[1].textContent).toContain("Current");
    expect(items[2].textContent).toContain("To do");
    for (const li of items) expect(li.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("marks nothing as current when every step is done", async () => {
    container = await mount(
      createElement(LoopStepper, { steps: STEPS.map((s) => ({ ...s, state: "done" })) })
    );
    expect(container.querySelectorAll('[aria-current="step"]')).toHaveLength(0);
  });

  it("renders nothing for no steps", async () => {
    container = await mount(createElement(LoopStepper, { steps: [] }));
    expect(container.querySelector("ol")).toBeNull();
  });
});

import { afterEach, describe, expect, it } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import ReadinessChip from "./ReadinessChip";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

async function render(props) {
  if (!root) {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  }
  await act(async () => {
    root.render(createElement(ReadinessChip, props));
  });
}

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  root = null;
});

const chip = () => container.firstElementChild;

describe("ReadinessChip", () => {
  it("reads N of M criteria passing", async () => {
    await render({ readiness: { passing: 1, total: 3, withoutTests: 0, allPassing: false } });
    expect(chip().textContent).toBe("1 of 3 criteria passing");
  });

  it("adds the number of criteria without tests", async () => {
    await render({ readiness: { passing: 1, total: 2, withoutTests: 2, allPassing: false } });
    expect(chip().textContent).toBe("1 of 2 criteria passing · 2 without tests");
  });

  it("says testing is done when every criterion with tests is passing", async () => {
    await render({ readiness: { passing: 2, total: 2, withoutTests: 0, allPassing: true } });
    expect(chip().textContent).toBe("All criteria passing. Testing is done for your saved tests.");
  });

  it("still lists criteria without tests next to the all-passing line", async () => {
    await render({ readiness: { passing: 2, total: 2, withoutTests: 1, allPassing: true } });
    expect(chip().textContent).toBe(
      "All criteria passing. Testing is done for your saved tests. · 1 without tests"
    );
  });

  it("is a status live region that stays mounted while its text changes", async () => {
    await render({ readiness: null });
    const live = chip();
    expect(live.getAttribute("role")).toBe("status");
    expect(live.textContent).toBe("");

    await render({ readiness: { passing: 0, total: 1, withoutTests: 0, allPassing: false } });
    expect(chip()).toBe(live);
    expect(live.textContent).toBe("0 of 1 criteria passing");

    await render({ readiness: { passing: 1, total: 1, withoutTests: 0, allPassing: true } });
    expect(chip()).toBe(live);
    expect(live.textContent).toContain("All criteria passing.");
  });

  it("can be plain text, so two chips on screen announce once", async () => {
    await render({ readiness: { passing: 1, total: 2, withoutTests: 0, allPassing: false }, live: false });
    expect(chip().getAttribute("role")).toBeNull();
    expect(chip().getAttribute("aria-live")).toBeNull();
    expect(chip().textContent).toBe("1 of 2 criteria passing");
  });
});

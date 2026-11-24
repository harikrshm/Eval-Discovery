import { afterEach, describe, expect, it } from "vitest";
import { createElement } from "react";
import RefreshFrame from "./RefreshFrame";
import { mount, rerender, unmount } from "./testUtils";

afterEach(unmount);

const frame = (refreshing) => createElement(RefreshFrame, { refreshing }, createElement("p", null, "numbers"));

describe("RefreshFrame", () => {
  it("is invisible while nothing is refreshing", async () => {
    const container = await mount(frame(false));
    expect(container.textContent).toBe("numbers");
    expect(container.querySelector("[aria-busy='true']")).toBeNull();
    expect(container.querySelector('[role="status"]')).toBeNull();
  });

  it("marks the content busy, dims it and says 'Updating…' while refreshing", async () => {
    const container = await mount(frame(true));
    const busy = container.querySelector('[aria-busy="true"]');
    expect(busy.textContent).toContain("numbers");
    expect(busy.textContent).toContain("Updating…");
    expect(container.querySelector('[role="status"]').textContent).toBe("Updating…");
    expect(container.querySelector("[data-refresh-content]").className).toContain("opacity-60");
    // Dimmed, not disabled: nothing blocks clicks.
    expect(container.innerHTML).not.toContain("pointer-events-none");
  });

  it("goes back to normal when the refresh ends", async () => {
    const container = await mount(frame(true));
    await rerender(frame(false));
    expect(container.querySelector('[aria-busy="true"]')).toBeNull();
    expect(container.textContent).toBe("numbers");
    expect(container.querySelector("[data-refresh-content]").className).not.toContain("opacity-60");
  });
});

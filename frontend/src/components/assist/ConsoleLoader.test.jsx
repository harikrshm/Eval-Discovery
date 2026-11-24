import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../lib/workspace", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, restoreAssistBackup: vi.fn() };
});

import { restoreAssistBackup } from "../../lib/workspace";
import ConsoleLoader from "./ConsoleLoader";
import { buttonByText, click, mount, unmount } from "./testUtils";

let container;
let errorSpy;

const consoleModule = { default: () => createElement("h1", null, "Console stub") };

async function renderLoader(load, onDisconnect = vi.fn()) {
  container = await mount(
    createElement("div", null, createElement("p", null, "The rest of the app"), createElement(ConsoleLoader, { load, onDisconnect }))
  );
  return onDisconnect;
}

beforeEach(() => {
  vi.mocked(restoreAssistBackup).mockClear();
  // React logs the error a boundary catches; that is expected here.
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(async () => {
  await unmount();
  errorSpy.mockRestore();
});

describe("ConsoleLoader", () => {
  it("shows a status line while the chunk loads and then the console", async () => {
    let release;
    await renderLoader(() => new Promise((resolve) => (release = resolve)));
    expect(container.querySelector('[role="status"]').textContent).toBe("Loading the console…");
    await act(async () => release(consoleModule));
    expect(container.querySelector("h1").textContent).toBe("Console stub");
  });

  it("shows a fallback for a chunk that fails to load and leaves the rest of the app standing", async () => {
    await renderLoader(() => Promise.reject(new Error("Failed to fetch dynamically imported module")));
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Couldn't load the console.");
    expect(alert.textContent).not.toContain("dynamically imported");
    expect(container.textContent).toContain("The rest of the app");
    expect(buttonByText(container, "Retry")).toBeDefined();
    expect(buttonByText(container, "Disconnect")).toBeDefined();
  });

  it("Retry loads the chunk again instead of reusing the failed attempt", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(consoleModule);
    await renderLoader(load);
    expect(load).toHaveBeenCalledTimes(1);
    await click(buttonByText(container, "Retry"));
    expect(load).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector("h1").textContent).toBe("Console stub");
  });

  it("shows the fallback again when Retry fails too", async () => {
    const load = vi.fn().mockRejectedValue(new Error("offline"));
    await renderLoader(load);
    await click(buttonByText(container, "Retry"));
    expect(load).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[role="alert"]').textContent).toContain("Couldn't load the console.");
  });

  it("Disconnect restores the user's workspace and leaves the console, as the console's own button does", async () => {
    const onDisconnect = await renderLoader(() => Promise.reject(new Error("offline")));
    await click(buttonByText(container, "Disconnect"));
    expect(restoreAssistBackup).toHaveBeenCalledTimes(1);
    expect(onDisconnect).toHaveBeenCalledTimes(1);
  });
});

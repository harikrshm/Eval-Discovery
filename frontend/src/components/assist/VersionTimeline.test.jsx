import { afterEach, describe, expect, it } from "vitest";
import { createElement } from "react";
import VersionTimeline from "./VersionTimeline";
import { mount, unmount, versionRow } from "./testUtils";

let container;

afterEach(async () => {
  await unmount();
});

async function render(versions) {
  container = await mount(createElement(VersionTimeline, { versions }));
  return [...container.querySelectorAll("li")];
}

describe("VersionTimeline", () => {
  it("lists the versions newest first, whatever order they arrive in", async () => {
    const items = await render([versionRow(1), versionRow(3), versionRow(2)]);
    expect(items.map((li) => li.querySelector("[data-version-number]").textContent)).toEqual(["v3", "v2", "v1"]);
  });

  it("shows origin, status and a UTC date, with fixed words only", async () => {
    const [item] = await render([
      versionRow(2, { origin: "suggested", status: "promoted", strength: "full", created_at: "2026-11-22T09:30:00" }),
    ]);
    expect(item.textContent).toContain("suggested fix");
    expect(item.textContent).toContain("2026-11-22 09:30 UTC");
  });

  it("marks only the current version, in words", async () => {
    const items = await render([
      versionRow(3, { status: "promoted", is_current: true, origin: "restored" }),
      versionRow(2, { status: "superseded" }),
    ]);
    expect(items[0].querySelector("[data-current]")?.textContent).toBe("Current");
    expect(items[0].getAttribute("aria-current")).toBe("true");
    expect(items[1].querySelector("[data-current]")).toBeNull();
    expect(items[1].getAttribute("aria-current")).toBeNull();
    expect(items[0].textContent).toContain("live");
    expect(items[1].textContent).toContain("earlier version");
  });

  it("names the other statuses the way the version history does", async () => {
    const items = await render([
      versionRow(5, { origin: "suggested", status: "proposed" }),
      versionRow(4, { origin: "suggested", status: "approved_verifying", strength: "full" }),
      versionRow(3, { origin: "suggested", status: "failed_verification", strength: "weaker" }),
      versionRow(2, { origin: "suggested", status: "rejected" }),
      versionRow(1, { origin: "suggested", status: "superseded" }),
    ]);
    const text = items.map((li) => li.textContent);
    expect(text[0]).toContain("proposed");
    expect(text[1]).toContain("verifying");
    expect(text[2]).toContain("failed verification");
    expect(text[3]).toContain("rejected");
    expect(text[4]).toContain("never approved");
  });

  it("falls back to the raw word for an unknown origin or status, and to n/a for a bad date", async () => {
    const [item] = await render([versionRow(1, { origin: "imported_x", status: "weird", created_at: "nope" })]);
    expect(item.textContent).toContain("imported_x");
    expect(item.textContent).toContain("weird");
    expect(item.textContent).toContain("n/a");
  });

  it("renders nothing for a missing list", async () => {
    container = await mount(createElement(VersionTimeline, { versions: null }));
    expect(container.querySelector("ol")).toBeNull();
  });
});

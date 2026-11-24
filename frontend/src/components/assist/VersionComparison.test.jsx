import { afterEach, describe, expect, it } from "vitest";
import { createElement } from "react";
import VersionComparison from "./VersionComparison";
import { mount, unmount } from "./testUtils";

let container;
afterEach(unmount);

async function render(comparison) {
  container = await mount(createElement(VersionComparison, { comparison }));
  return container.querySelector('section[aria-labelledby="versions-heading"]');
}

const AVAILABLE = {
  available: true,
  note: "Real (non-imported) questions only.",
  versions: {
    1: {
      questions: 12,
      reask_rate: 0.25,
      thumbs_down_rate: 0.1667,
      enough_data: true,
      intents: { claim_explain: { questions: 8, reask_rate: 0.5, thumbs_down_rate: null } },
    },
    2: { questions: 14, reask_rate: 0.1, thumbs_down_rate: 0, enough_data: true, intents: {} },
    3: {
      questions: 4,
      reask_rate: 1,
      thumbs_down_rate: 1,
      enough_data: false,
      intents: { other: { questions: 4, reask_rate: 1, thumbs_down_rate: 1 } },
    },
  },
};

describe("VersionComparison", () => {
  it("renders nothing without a comparison", async () => {
    container = await mount(createElement(VersionComparison, { comparison: null }));
    expect(container.querySelector("section")).toBeNull();
  });

  it("gives one sentence and no table when it is not available", async () => {
    const section = await render({
      available: false,
      reason: "All questions are imported demo data, so versions cannot be compared.",
    });
    expect(section.textContent).toContain("All questions are imported demo data, so versions cannot be compared.");
    expect(section.querySelector("table")).toBeNull();
  });

  it("says in words that only real (non-imported) questions are compared", async () => {
    const section = await render(AVAILABLE);
    expect(section.textContent).toContain("Real (non-imported) questions only.");
    const unavailable = await render({ available: false, reason: "Not enough real data." });
    expect(unavailable.textContent).toContain("Real (non-imported) questions only.");
  });

  it("shows one row per version with its question count and rates", async () => {
    const section = await render(AVAILABLE);
    const rows = [...section.querySelectorAll("tbody tr")];
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toBe("v11225%16.7%");
    expect(rows[1].textContent).toBe("v214" + "10%" + "0%");
    expect(rows[0].getAttribute("data-enough-data")).toBe("true");
  });

  it("greys versions with too few questions and shows no rate for them, not even 100%", async () => {
    const section = await render(AVAILABLE);
    const row = section.querySelectorAll("tbody tr")[2];
    expect(row.getAttribute("data-enough-data")).toBe("false");
    expect(row.className).toContain("opacity-60");
    expect(row.textContent).toContain("too few questions to compare");
    expect(row.textContent).not.toContain("100%");
  });

  it("lists per-intent rates only for versions with enough data, with n/a for a missing rate", async () => {
    const section = await render(AVAILABLE);
    const items = [...section.querySelectorAll("details li")].map((li) => li.textContent);
    expect(items).toEqual(["v1 · claim explain: 8 questions, re-ask 50%, thumbs-down n/a"]);
  });
});

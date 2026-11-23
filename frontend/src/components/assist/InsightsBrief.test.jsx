import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, assistInsights: vi.fn() };
});

import * as api from "../../api";
import InsightsBrief from "./InsightsBrief";
import { buttonByText, click, mount, settle, unmount } from "./testUtils";

let container;

const RESULT = {
  brief: "Claims get the most questions.\nNavigation is hard.",
  counts_used: { "totals.questions": 12, "struggle.refusal_count": 1 },
  data_source_label: "demo dataset",
  grounded: true,
  warnings: [],
};

beforeEach(async () => {
  vi.resetAllMocks();
  container = await mount(createElement(InsightsBrief));
});

afterEach(unmount);

describe("InsightsBrief", () => {
  it("disables the button while the request is pending", async () => {
    let resolve;
    api.assistInsights.mockReturnValue(new Promise((r) => (resolve = r)));
    const button = buttonByText(container, "Generate insights");
    expect(button.disabled).toBe(false);

    await click(button);
    expect(button.disabled).toBe(true);
    expect(container.querySelector('[role="status"]').textContent).toContain("Generating insights");
    // A second click while pending does nothing.
    await click(button);
    expect(api.assistInsights).toHaveBeenCalledTimes(1);

    await act(async () => resolve(RESULT));
    await settle();
    expect(button.disabled).toBe(false);
  });

  it("renders the brief as preformatted plain text and lists the figures used", async () => {
    api.assistInsights.mockResolvedValue(RESULT);
    await click(buttonByText(container, "Generate insights"));
    const brief = container.querySelector('[data-testid="insights-brief"]');
    expect(brief.textContent).toBe(RESULT.brief);
    expect(brief.className).toContain("whitespace-pre-wrap");
    const details = container.querySelector("details");
    expect(details.querySelector("summary").textContent).toBe("Figures used");
    expect(details.textContent).toContain("totals.questions: 12");
    expect(details.textContent).toContain("struggle.refusal_count: 1");
    expect(details.open).toBe(false);
    expect(container.textContent).not.toContain("Check these figures");
  });

  it("never turns the brief into elements, links or markdown", async () => {
    const hostile = [
      "<img src=x onerror=alert(1)>",
      "**bold** and [a link](https://example.com) and https://example.org",
      "# Heading",
      "<script>alert(2)</script>",
    ].join("\n");
    api.assistInsights.mockResolvedValue({ ...RESULT, brief: hostile });
    await click(buttonByText(container, "Generate insights"));
    const brief = container.querySelector('[data-testid="insights-brief"]');
    expect(brief.textContent).toBe(hostile);
    expect(brief.children).toHaveLength(0);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("strong, b, h1")).toBeNull();
  });

  it("warns to check the figures, with the warnings as plain text, when the brief is not grounded", async () => {
    api.assistInsights.mockResolvedValue({
      ...RESULT,
      grounded: false,
      warnings: ["The figure 99 could not be matched.", "<b>bold</b>"],
    });
    await click(buttonByText(container, "Generate insights"));
    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain(
      "Check these figures: some numbers in this summary could not be matched to the data."
    );
    expect(alert.textContent).toContain("The figure 99 could not be matched.");
    expect(alert.textContent).toContain("<b>bold</b>");
    expect(alert.querySelector("b")).toBeNull();
  });

  it("shows the not-enough-data text and other server errors as plain text", async () => {
    api.assistInsights.mockRejectedValueOnce(new api.ApiError("Not enough data yet.", 409));
    await click(buttonByText(container, "Generate insights"));
    expect(container.querySelector('[role="alert"]').textContent).toBe("Not enough data yet.");
    expect(container.querySelector('[data-testid="insights-brief"]')).toBeNull();

    api.assistInsights.mockRejectedValueOnce(
      new api.ApiError("The insights could not be generated right now. Please try again.", 502)
    );
    await click(buttonByText(container, "Generate insights"));
    expect(container.querySelector('[role="alert"]').textContent).toBe(
      "The insights could not be generated right now. Please try again."
    );

    api.assistInsights.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await click(buttonByText(container, "Generate insights"));
    expect(container.querySelector('[role="alert"]').textContent).toBe(
      "The insights could not be generated right now. Please try again."
    );
  });

  it("clears an earlier brief when the next request fails", async () => {
    api.assistInsights.mockResolvedValueOnce(RESULT);
    await click(buttonByText(container, "Generate insights"));
    api.assistInsights.mockRejectedValueOnce(new api.ApiError("Too many requests.", 429));
    await click(buttonByText(container, "Generate insights"));
    expect(container.querySelector('[data-testid="insights-brief"]')).toBeNull();
    expect(container.querySelector('[role="alert"]').textContent).toBe("Too many requests.");
  });
});

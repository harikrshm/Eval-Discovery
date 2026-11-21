import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import PromptEditor from "./PromptEditor";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

async function render(props) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      createElement(PromptEditor, {
        promptText: "You are Alex.",
        versionNumber: 2,
        onSave: vi.fn(async () => {}),
        ...props,
      })
    );
  });
}

function buttonByText(text) {
  return [...container.querySelectorAll("button")].find((b) => b.textContent.includes(text));
}

async function openEditor() {
  await act(async () => {
    buttonByText("Edit prompt").click();
  });
}

async function type(value) {
  const textarea = container.querySelector("textarea");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    setter.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("PromptEditor", () => {
  it("shows which prompt version the chat uses and hides the editor until asked", async () => {
    await render({});
    expect(container.textContent).toContain("Chat uses v2");
    expect(container.querySelector("textarea")).toBeNull();
    expect(buttonByText("Edit prompt").getAttribute("aria-expanded")).toBe("false");
  });

  it("opens a labelled textarea with the current prompt and a character counter", async () => {
    await render({});
    await openEditor();

    const textarea = container.querySelector("textarea");
    expect(textarea.value).toBe("You are Alex.");
    const label = container.querySelector(`label[for="${textarea.id}"]`);
    expect(label.textContent).toBe("System prompt");
    expect(container.textContent).toContain("13 / 4000");
    expect(buttonByText("Edit prompt").getAttribute("aria-expanded")).toBe("true");
  });

  it("disables Save when the text is unchanged, blank or over the limit", async () => {
    await render({});
    await openEditor();
    const save = () => buttonByText("Save");

    expect(save().disabled).toBe(true); // unchanged

    await type("   ");
    expect(save().disabled).toBe(true); // blank

    await type("x".repeat(4001));
    expect(save().disabled).toBe(true); // over the limit
    expect(container.textContent).toContain("4001 / 4000");

    await type("x".repeat(4000));
    expect(save().disabled).toBe(false);

    await type("You are Alex, a careful agent.");
    expect(save().disabled).toBe(false);
  });

  it("saves the trimmed text, closes the editor and returns focus to Edit prompt", async () => {
    const onSave = vi.fn(async () => {});
    await render({ onSave });
    await openEditor();
    await type("  You are Alex, a careful agent.  ");

    await act(async () => {
      buttonByText("Save").click();
    });

    expect(onSave).toHaveBeenCalledWith("You are Alex, a careful agent.");
    expect(container.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(buttonByText("Edit prompt"));
  });

  it("keeps Save disabled and busy for the whole request so a double click saves once", async () => {
    let finish;
    const onSave = vi.fn(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    await render({ onSave });
    await openEditor();
    await type("A new prompt.");

    await act(async () => {
      buttonByText("Save").click();
    });
    const busy = buttonByText("Saving");
    expect(busy.disabled).toBe(true);
    expect(busy.getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      busy.click();
      busy.click();
    });
    expect(onSave).toHaveBeenCalledTimes(1);

    await act(async () => {
      finish();
    });
    expect(container.querySelector("textarea")).toBeNull();
  });

  it("shows the message of a failed save in an alert and keeps the editor open", async () => {
    const onSave = vi.fn(async () => {
      throw new Error("Prompt version limit reached for this agent.");
    });
    await render({ onSave });
    await openEditor();
    await type("A new prompt.");

    await act(async () => {
      buttonByText("Save").click();
    });

    const alert = container.querySelector('[role="alert"]');
    expect(alert.textContent).toContain("Prompt version limit reached for this agent.");
    expect(container.querySelector("textarea").value).toBe("A new prompt.");
    expect(buttonByText("Save").disabled).toBe(false); // can retry
  });

  it("restores the saved prompt and focus on Cancel", async () => {
    const onSave = vi.fn();
    await render({ onSave });
    await openEditor();
    await type("Something else");

    await act(async () => {
      buttonByText("Cancel").click();
    });

    expect(onSave).not.toHaveBeenCalled();
    expect(container.querySelector("textarea")).toBeNull();
    expect(document.activeElement).toBe(buttonByText("Edit prompt"));

    await openEditor();
    expect(container.querySelector("textarea").value).toBe("You are Alex.");
  });
});

describe("PromptEditor announcements", () => {
  const status = () => container.querySelector('[role="status"]');

  it("has an empty status region before anything is saved", async () => {
    await render({});
    expect(status()).not.toBeNull();
    expect(status().textContent).toBe("");
  });

  it("announces the saved version and clears it when editing again", async () => {
    const onSave = vi.fn(async () => {});
    await render({ onSave, versionNumber: 3 });
    await openEditor();
    await type("A different prompt.");
    await act(async () => {
      buttonByText("Save").click();
    });
    expect(status().textContent).toBe("Saved as v3.");
    expect(status().className).toContain("sr-only");

    await openEditor();
    expect(status().textContent).toBe("");
  });

  it("announces nothing when the save fails", async () => {
    const onSave = vi.fn(async () => {
      throw new Error("nope");
    });
    await render({ onSave });
    await openEditor();
    await type("A different prompt.");
    await act(async () => {
      buttonByText("Save").click();
    });
    expect(status().textContent).toBe("");
  });
});

describe("PromptEditor active version", () => {
  it("shows only the live version when nothing is being verified", async () => {
    await render({ versionNumber: 2 });
    expect(container.textContent).toContain("Chat uses v2");
    expect(container.textContent).not.toContain("verifying");
    expect(container.textContent).not.toContain("A fix is being verified.");
  });

  it("names the candidate while it is verified, and blocks editing with plain-text help", async () => {
    await render({ versionNumber: 2, verifyingNumber: 3 });
    expect(container.textContent).toContain("Chat uses v2 · v3 verifying");
    expect(buttonByText("Edit prompt").disabled).toBe(true);
    const help = [...container.querySelectorAll("p")].find((p) =>
      p.textContent.includes("A fix is being verified. Wait for it to finish.")
    );
    expect(help).toBeDefined();
    expect(help.getAttribute("role")).toBeNull();
  });

  it("disables Save while a fix is being verified, even with an edit in progress", async () => {
    await render({ versionNumber: 2, verifyingNumber: null });
    await openEditor();
    await type("A different prompt.");
    expect(buttonByText("Save").disabled).toBe(false);

    await act(async () => {
      root.render(createElement(PromptEditor, { promptText: "You are Alex.", versionNumber: 2, verifyingNumber: 3, onSave: vi.fn() }));
    });
    expect(buttonByText("Save").disabled).toBe(true);
    expect(container.textContent).toContain("A fix is being verified. Wait for it to finish.");
    expect(buttonByText("Cancel").disabled).toBe(false);
  });
});

describe("PromptEditor stale prompt protection", () => {
  const FAILED = "Couldn't load the latest prompt. Try again.";

  it("refreshes first and opens the editor on the refreshed prompt", async () => {
    let finish;
    const onBeforeEdit = vi.fn(() => new Promise((resolve) => (finish = resolve)));
    await render({ onBeforeEdit });
    await openEditor();
    expect(onBeforeEdit).toHaveBeenCalledTimes(1);
    expect(container.querySelector("textarea")).toBeNull();
    expect(buttonByText("Edit prompt").disabled).toBe(true);
    expect(buttonByText("Edit prompt").getAttribute("aria-busy")).toBe("true");

    await act(async () => {
      root.render(createElement(PromptEditor, { promptText: "You are Alex, careful.", versionNumber: 3, onSave: vi.fn(), onBeforeEdit }));
      finish(true);
    });
    expect(container.querySelector("textarea").value).toBe("You are Alex, careful.");
    expect(container.textContent).toContain("Chat uses v3");
  });

  it("refuses to open when the refresh failed and says so in an alert", async () => {
    const onBeforeEdit = vi.fn(async () => false);
    await render({ onBeforeEdit });
    await openEditor();
    expect(container.querySelector("textarea")).toBeNull();
    expect(container.querySelector('[role="alert"]').textContent).toBe(FAILED);
    expect(buttonByText("Edit prompt").disabled).toBe(false);
    expect(buttonByText("Edit prompt").getAttribute("aria-expanded")).toBe("false");
  });

  it("tries again on the next click and clears the alert when it works", async () => {
    const onBeforeEdit = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    await render({ onBeforeEdit });
    await openEditor();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    await openEditor();
    expect(onBeforeEdit).toHaveBeenCalledTimes(2);
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(container.querySelector("textarea")).not.toBeNull();
  });

  it("opens straight away when no refresh is wired", async () => {
    await render({});
    await openEditor();
    expect(container.querySelector("textarea")).not.toBeNull();
  });

  it("ignores a second click while the refresh is in flight", async () => {
    let finish;
    const onBeforeEdit = vi.fn(() => new Promise((resolve) => (finish = resolve)));
    await render({ onBeforeEdit });
    await act(async () => {
      buttonByText("Edit prompt").click();
      buttonByText("Edit prompt").click();
    });
    expect(onBeforeEdit).toHaveBeenCalledTimes(1);
    await act(async () => finish(true));
  });
});

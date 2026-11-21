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
    expect(container.textContent).toContain("Chat uses prompt v2");
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

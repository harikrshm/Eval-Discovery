import { describe, expect, it } from "vitest";
import { remainingDraft } from "./draft.js";

const m = (content, role = "user") => ({ role, content });

describe("remainingDraft", () => {
  it("returns [] when nothing was added after the snapshot", () => {
    expect(remainingDraft([m("a"), m("b", "assistant"), m("c")], 3)).toEqual([]);
  });

  it("returns exactly the messages added after the snapshot, in order", () => {
    const draft = [m("a"), m("b", "assistant"), m("c"), m("d"), m("e", "assistant")];
    expect(remainingDraft(draft, 3)).toEqual([m("d"), m("e", "assistant")]);
  });

  it("returns [] when savedCount exceeds the draft length", () => {
    expect(remainingDraft([m("a")], 5)).toEqual([]);
  });
});

import { beforeEach } from "vitest";
import { clearAllDrafts, draftKey } from "./draft.js";

describe("clearAllDrafts", () => {
  beforeEach(() => sessionStorage.clear());

  it("removes every stored draft and nothing else", () => {
    sessionStorage.setItem(draftKey("a1"), "[]");
    sessionStorage.setItem(draftKey("a2"), "[]");
    sessionStorage.setItem("other", "keep");
    clearAllDrafts();
    expect(sessionStorage.getItem(draftKey("a1"))).toBeNull();
    expect(sessionStorage.getItem(draftKey("a2"))).toBeNull();
    expect(sessionStorage.getItem("other")).toBe("keep");
  });
});

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

import { describe, expect, it } from "vitest";
import { diffLines } from "./diff.js";

const same = (text) => ({ type: "same", text });
const added = (text) => ({ type: "added", text });
const removed = (text) => ({ type: "removed", text });

describe("diffLines", () => {
  it("marks identical text as all same", () => {
    expect(diffLines("a\nb\nc", "a\nb\nc")).toEqual([same("a"), same("b"), same("c")]);
  });

  it("reports a pure addition", () => {
    expect(diffLines("a\nc", "a\nb\nc")).toEqual([same("a"), added("b"), same("c")]);
    expect(diffLines("a", "a\nb")).toEqual([same("a"), added("b")]);
  });

  it("reports a pure removal", () => {
    expect(diffLines("a\nb\nc", "a\nc")).toEqual([same("a"), removed("b"), same("c")]);
    expect(diffLines("a\nb", "b")).toEqual([removed("a"), same("b")]);
  });

  it("shows a changed middle line as a removal then an addition", () => {
    expect(diffLines("a\nold\nc", "a\nnew\nc")).toEqual([same("a"), removed("old"), added("new"), same("c")]);
  });

  it("handles empty strings", () => {
    expect(diffLines("", "")).toEqual([]);
    expect(diffLines("", "a\nb")).toEqual([added("a"), added("b")]);
    expect(diffLines("a\nb", "")).toEqual([removed("a"), removed("b")]);
    expect(diffLines(null, undefined)).toEqual([]);
  });

  it("normalizes \r\n so line endings alone are not a change", () => {
    expect(diffLines("a\r\nb\r\n", "a\nb\n")).toEqual([same("a"), same("b"), same("")]);
    expect(diffLines("a\r\nb", "a\nc")).toEqual([same("a"), removed("b"), added("c")]);
  });

  it("keeps document order across several separate changes", () => {
    expect(diffLines("1\n2\n3\n4\n5", "1\nX\n3\n5\nY")).toEqual([
      same("1"),
      removed("2"),
      added("X"),
      same("3"),
      removed("4"),
      same("5"),
      added("Y"),
    ]);
  });

  it("handles repeated lines", () => {
    expect(diffLines("a\na\nb", "a\nb\nb")).toEqual([same("a"), removed("a"), same("b"), added("b")]);
  });
});

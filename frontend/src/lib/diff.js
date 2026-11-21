// A small line-based diff (longest common subsequence). Prompts are at most a few thousand
// characters, so the quadratic table is tiny. Returns lines in document order as
// { type: "same" | "added" | "removed", text }; a changed line is a removal then an addition.

function toLines(text) {
  if (text == null || text === "") return [];
  return String(text).replace(/\r\n/g, "\n").split("\n");
}

export function diffLines(oldText, newText) {
  const a = toLines(oldText);
  const b = toLines(newText);

  // lcs[i][j] = length of the common subsequence of a[i..] and b[j..].
  const lcs = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const out = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ type: "same", text: a[i] });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ type: "removed", text: a[i] });
      i += 1;
    } else {
      out.push({ type: "added", text: b[j] });
      j += 1;
    }
  }
  for (; i < a.length; i += 1) out.push({ type: "removed", text: a[i] });
  for (; j < b.length; j += 1) out.push({ type: "added", text: b[j] });
  return out;
}

// What the before/after replay chart is built from: the stored outcome of each verified prompt
// version (`GET /prompt-versions/{id}/report` -> `outcome.table`, one row per required test with the
// verdict before and after the fix). Nothing is generated or estimated: a version with no stored
// outcome has no bars. Only verdict words are read; the rows' rationale and transcript never are.

export const MAX_REPLAY_VERSIONS = 8;

// A version that went through approval has a verification record (the list shows it as `strength`);
// the ones whose outcome is stored are those that finished, whichever way.
const FINISHED = ["promoted", "failed_verification", "superseded"];

// The newest verified, finished versions (a bounded number of reads), oldest first.
export function replayCandidates(versions) {
  if (!Array.isArray(versions)) return [];
  return versions
    .filter((v) => v && typeof v.strength === "string" && FINISHED.includes(v.status))
    .sort((a, b) => b.number - a.number)
    .slice(0, MAX_REPLAY_VERSIONS)
    .reverse();
}

// How many tests passed on one side, or null when no test has a verdict there (not a zero).
function passed(table, side) {
  const known = table.filter((row) => typeof row?.[side] === "string");
  return known.length === 0 ? null : known.filter((row) => row[side] === "pass").length;
}

export function replayRow(version, report) {
  const table = report?.outcome?.table;
  if (!Array.isArray(table) || table.length === 0) return null;
  const before = passed(table, "before");
  const after = passed(table, "after");
  if (before === null && after === null) return null;
  return { number: version.number, label: `v${version.number}`, before, after, total: table.length };
}

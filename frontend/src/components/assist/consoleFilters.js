// The filters every console tab shares: `{ range, source, version }`.

export const DEFAULT_FILTERS = { range: "30d", source: "all", version: null };

const BADGES = {
  all: "Data: Live + demo dataset",
  live: "Data: Live only",
  demo: "Data: Demo dataset",
};

export function sourceBadgeText(source) {
  return BADGES[source] ?? BADGES.all;
}


const RANGE_WORDS = { "24h": "last 24 hours (UTC)", "7d": "last 7 days (UTC)", "30d": "last 30 days (UTC)", all: "all time" };
const SOURCE_WORDS = { all: "live and demo dataset", live: "live", demo: "demo dataset" };

// The active filters in words, e.g. "last 7 days (UTC), source: live, version 3".
export function scopeText(filters) {
  const { range, source, version } = filters ?? {};
  const parts = [RANGE_WORDS[range] ?? RANGE_WORDS.all, `source: ${SOURCE_WORDS[source] ?? SOURCE_WORDS.all}`];
  if (version !== null && version !== undefined) parts.push(`version ${version}`);
  return parts.join(", ");
}

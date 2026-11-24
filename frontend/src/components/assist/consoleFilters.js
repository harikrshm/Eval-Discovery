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


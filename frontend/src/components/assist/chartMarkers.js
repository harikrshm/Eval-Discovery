// Version markers from the console payloads ({ at, version, source, label }) as TrendChart markers.
// The backend label already says "(demo)" for generated data; the chart adds it from `demo`, so it is
// stripped here to appear once.
export function chartMarkers(markers) {
  return (markers ?? []).map((m) => ({
    at: m.at,
    label: String(m.label ?? `v${m.version}`).replace(/ \(demo\)$/, ""),
    demo: m.source === "demo",
  }));
}

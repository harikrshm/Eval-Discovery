// Plain-text formatting for the console. A missing value is always "n/a": a rate whose denominator
// is zero is not a zero.

export const NA = "n/a";

export function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

// 0.4286 -> "42.9%"
export function pct(value) {
  return isNumber(value) ? `${round1(value * 100)}%` : NA;
}

// A figure that is already a rate per 100 views (it can be above 100).
export function per100(value) {
  return isNumber(value) ? String(round1(value)) : NA;
}

// "legal_events" -> "legal events"
export function humanize(key) {
  return String(key).replaceAll("_", " ");
}

export function shortId(id) {
  if (typeof id !== "string" || !id) return "no session";
  return id.length > 8 ? id.slice(0, 8) : id;
}

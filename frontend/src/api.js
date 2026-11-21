const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function sendMessage(messages) {
  const res = await fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });
  if (!res.ok) throw new Error(`Chat failed: ${res.status}`);
  return res.json();
}

export async function checkPattern(transcripts) {
  const res = await fetch(`${BASE_URL}/check-pattern`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcripts }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Pattern check failed: ${res.status}`);
  }
  return res.json(); // { found: bool, pattern_summary: string }
}

export async function generateCriteria(patternSummary) {
  const res = await fetch(`${BASE_URL}/generate-criteria`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pattern_summary: patternSummary }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Criteria generation failed: ${res.status}`);
  }
  return res.json(); // { name, definition, good_example, bad_example, why_discovered }
}

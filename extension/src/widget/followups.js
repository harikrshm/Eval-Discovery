// One suggested follow-up question per answer, chosen from a fixed table by the answer's intent,
// and the point in a conversation at which the widget asks for feedback once.

export const FEEDBACK_AFTER_QUESTIONS = 3

const CPC = 'What do these CPC codes mean?'
const FORCE = 'Is this still in force?'
const CLAIM1 = 'Explain claim 1'
const CLAIM_CANDIDATE = /^(?:What does claim|Explain claim) (\d+)(?: add| cover)?\??$/

const norm = (text) => String(text).trim().toLowerCase()

/** The candidate questions for an intent, in order. `n` is the claim after the highest one cited (null: none cited or none after it). */
function candidatesFor(intent, n) {
  switch (intent) {
    case 'claim_explain':
    case 'claim_scope':
      return [...(n === null ? [] : [`What does claim ${n} add?`]), CPC, FORCE]
    case 'classification':
      return [FORCE, 'Who cites this patent?']
    case 'status_family':
      return [CPC, CLAIM1]
    case 'citations_prior_art':
      return [CLAIM1, FORCE]
    case 'legal_opinion_request':
      return ['What does claim 1 cover?', FORCE]
    default:
      return [CLAIM1, CPC, FORCE]
  }
}

/**
 * The follow-up to offer after an answer, or null. A candidate is skipped when it was already asked
 * (trimmed, case-insensitive) or names a claim the patent does not have.
 * @param {{intent: string, references: Array<{kind: string, target: string}>, asked: string[], claimNumbers: number[]}} input
 */
export function pickFollowUp({ intent, references, asked, claimNumbers } = {}) {
  const numbers = Array.isArray(claimNumbers) ? claimNumbers.filter(Number.isInteger) : []
  const done = new Set((Array.isArray(asked) ? asked : []).map(norm))
  let highest = 0
  for (const ref of Array.isArray(references) ? references : []) {
    if (ref?.kind === 'claim') {
      const num = parseInt(ref.target, 10)
      if (Number.isInteger(num) && num > highest) highest = num
    }
  }
  // A claim follow-up is only offered once the answer cited a claim.
  const next = highest > 0 ? numbers.filter((c) => c > highest).sort((a, b) => a - b)[0] : undefined
  for (const candidate of candidatesFor(intent, next ?? null)) {
    if (done.has(norm(candidate))) continue
    const claim = CLAIM_CANDIDATE.exec(candidate)
    if (claim && !numbers.includes(parseInt(claim[1], 10))) continue
    return candidate
  }
  return null
}

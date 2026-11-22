// JavaScript port of the claim-reference rules in backend/core/assist/brief.py
// (parse_claim_refs / iter_claim_ref_ranges / build_claim_tree). Both
// implementations are tested against tests/fixtures/claim_ref_cases.json.
//
// Known, accepted divergences (none is in the shared cases): Python's IGNORECASE is
// Unicode-aware, so U+017F (long s) and U+212A (Kelvin sign) match inside "claims" there
// and not in JavaScript; \s matches U+FEFF in JavaScript only and U+001C-U+001F and
// U+0085 in Python only.

const MAX_NUMBER_DIGITS = 6

const NUM = `[0-9]{1,${MAX_NUMBER_DIGITS}}(?![0-9])`
const RANGE_SEP = '(?:\\s*[-\\u2013\\u2014]\\s*|\\s+(?:to|through)\\s+)'
const ITEM = `${NUM}(?:${RANGE_SEP}${NUM})?`
const LIST_SEP = '(?:\\s*,\\s*(?:(?:and|or)\\s+)?|\\s+(?:and|or)\\s+)'
// ASCII-only word boundary: letters, digits and "_" are word characters.
const REF_SOURCE = `(?<![A-Za-z0-9_])claims?\\s+(${ITEM}(?:${LIST_SEP}${ITEM})*)`
const ITEM_SOURCE = `([0-9]+)(?:${RANGE_SEP}([0-9]+))?`

/** Yield [start, end] for every item of every claim reference in `text`. */
export function* iterClaimRefRanges(text) {
  const refRe = new RegExp(REF_SOURCE, 'gi')
  for (const ref of String(text).matchAll(refRe)) {
    const itemRe = new RegExp(ITEM_SOURCE, 'g')
    for (const item of ref[1].matchAll(itemRe)) {
      const start = parseInt(item[1], 10)
      yield [start, item[2] ? parseInt(item[2], 10) : start]
    }
  }
}

/** Sorted, de-duplicated lower-numbered claims that `text` references. */
export function parseClaimRefs(number, text) {
  const refs = new Set()
  for (const [start, end] of iterClaimRefRanges(text)) {
    const hi = Math.min(end, number - 1)
    for (let n = Math.max(start, 1); n <= hi; n++) refs.add(n)
  }
  return [...refs].sort((a, b) => a - b)
}

/** Claim objects (input order) with depends_on and independent set from the text. */
export function buildClaimTree(claims) {
  return claims.map(({ number, text }) => {
    const dependsOn = parseClaimRefs(number, text)
    return { number, text, depends_on: dependsOn, independent: dependsOn.length === 0 }
  })
}

// ---- patent numbers ------------------------------------------------------

const US_RE = /^US[0-9]{6,11}(?:[A-Z][0-9]?)?$/i

/** True for compact US numbers such as US10000000B2 or US20160266243A1 (ASCII only). */
export function isUsPatentNumber(patentNumber) {
  return typeof patentNumber === 'string' && US_RE.test(patentNumber.trim())
}

/** "US 10,000,000 B2" -> "US10000000B2": drop whitespace, commas and hyphens, upper-case. */
export function normalizePatentNumber(text) {
  if (typeof text !== 'string') return ''
  return text.replace(/[\s,\-‐-―]+/g, '').toUpperCase()
}

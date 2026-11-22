import { describe, expect, it } from 'vitest'
import { extractBrief } from './extractor.js'

const COMPLETENESS_KEYS = [
  'bibliographic', 'abstract', 'claims', 'description', 'classifications',
  'landscapes', 'citations', 'family', 'legal_events',
]

describe('garbage and partial input', () => {
  it('garbage HTML yields a mostly empty brief and does not throw', () => {
    const doc = new DOMParser().parseFromString('<div><p>not a patent <b>at all', 'text/html')
    const brief = extractBrief(doc, 'https://example.com/')
    expect(brief.patent_number).toBe('')
    expect(brief.claims).toEqual([])
    expect(brief.description).toEqual([])
    expect(Object.keys(brief.completeness).sort()).toEqual([...COMPLETENESS_KEYS].sort())
    expect(Object.values(brief.completeness).every((v) => v === false)).toBe(true)
  })
  it('never throws on null input', () => {
    expect(() => extractBrief(null, undefined)).not.toThrow()
    expect(() => extractBrief(undefined, 42)).not.toThrow()
    const brief = extractBrief(null, 'https://patents.google.com/patent/US6756997B1/en')
    expect(brief.patent_number).toBe('US6756997B1')
  })
  it('takes the number from the URL path first', () => {
    const doc = new DOMParser().parseFromString('<h2 id="pubnum">US1B1</h2>', 'text/html')
    const brief = extractBrief(doc, 'https://patents.google.com/patent/US6756997B1/en?oq=x#frag')
    expect(brief.patent_number).toBe('US6756997B1')
  })
  it('falls back to the page number', () => {
    const doc = new DOMParser().parseFromString('<h2 id="pubnum">US 10,000,000 B2</h2>', 'text/html')
    expect(extractBrief(doc, 'https://patents.google.com/').patent_number).toBe('US10000000B2')
  })
})

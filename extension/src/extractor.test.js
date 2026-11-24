import { describe, expect, it } from 'vitest'
import { CAPS, extractBrief } from './extractor.js'
import { load } from './testutil.js'

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

describe('paragraph markers in the captured description', () => {
  const descText = (b) => b.description.map((s) => s.text).join('\n\n')
  const synth = (inner) =>
    extractBrief(
      new DOMParser().parseFromString(`<patent-text name="description"><section id="text">${inner}</section></patent-text>`, 'text/html'),
      'https://patents.google.com/patent/US1B1/en',
    )
  it('granted patent with num attributes: markers are present, text not doubled', () => {
    const { doc } = load('us12401720b1.html')
    const text = descText(extractBrief(doc, 'https://patents.google.com/patent/US12401720B1/en'))
    expect(text).toContain('[p0001] This disclosure relates generally to an apparatus')
    expect(text).toContain('[p0002] The Internet is a global system')
    expect(text).not.toMatch(/\[p0001\] 0001/)
  })
  it.each([
    ['us20250119367a1.html', 'US20250119367A1'],
    ['us6756997b1.html', 'US6756997B1'],
  ])('%s has no markers', (file, number) => {
    const { doc } = load(file)
    expect(descText(extractBrief(doc, `https://patents.google.com/patent/${number}/en`))).not.toMatch(/\[p[0-9]+\]/)
  })
  it('a synthetic paragraph with num="0042" starts with [p0042] ', () => {
    const b = synth('<div class="description-paragraph" num="0042">Hello there.</div>')
    expect(b.description[0].text.startsWith('[p0042] Hello there.')).toBe(true)
  })
  it('a paragraph with num="abc" or no num gets no marker; description-line is unchanged', () => {
    const b = synth(
      '<div class="description-paragraph" num="abc">Alpha.</div><div class="description-paragraph">Beta.</div><div class="description-line" num="0007">Gamma.</div>',
    )
    expect(b.description[0].text).toBe('Alpha.\n\nBeta.\n\nGamma.')
  })
  it('the marker counts toward the section cap', () => {
    const big = 'x'.repeat(5000)
    const b = synth(`<div class="description-paragraph" num="0001">${big}</div><div class="description-paragraph" num="0002">${big}</div>`)
    expect(b.description.length).toBeGreaterThan(0)
    for (const s of b.description) expect(s.text.length).toBeLessThanOrEqual(CAPS.sectionChars)
  })
})

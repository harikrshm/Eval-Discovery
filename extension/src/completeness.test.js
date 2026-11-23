// Final fix wave I1: a section that was capped or shrunk must be reported as incomplete.
import { describe, expect, it } from 'vitest'
import { CAPS, extractBrief, fitBrief } from './extractor.js'

const url = 'https://patents.google.com/patent/US1B1/en'
const parse = (html) => new DOMParser().parseFromString(html, 'text/html')
const body = (inner) => parse(`<body><h1 id="title">T</h1>${inner}</body>`)

const tableRows = (n) => Array.from({ length: n }, (_, i) => `<div class="tr"><span class="td"><a>US${i}A</a></span><span class="td">d</span><span class="td">2000-01-01</span><span class="td">a</span><span class="td">t</span></div>`).join('')
const table = (id, n) => `<h3 id="${id}">x</h3><div class="responsive-table"><div class="tbody">${tableRows(n)}</div></div>`
const claimDiv = (n) => `<div num="${n}" class="claim"><div class="claim-text">${n}. A thing.</div></div>`
const claimsPage = (n) => body(`<patent-text name="claims"><section class="flex"><div class="claims">${Array.from({ length: n }, (_, i) => claimDiv(i + 1)).join('')}</div></section></patent-text>`)
const tree = (n) => `<classification-tree><concept-mention><state-modifier class="code" data-cpc="C${n}"></state-modifier><span class="description">d</span></concept-mention></classification-tree>`

describe('completeness is false for every capped or shrunk section', () => {
  it('558 cited-by rows keep 300 and report citations incomplete', () => {
    const b = extractBrief(body(table('citedBy', 558)), url)
    expect(b.citations.cited_by).toHaveLength(CAPS.listItems)
    expect(b.completeness.citations).toBe(false)
  })
  it('exactly 300 rows is still complete', () => {
    const b = extractBrief(body(table('citedBy', 300)), url)
    expect(b.citations.cited_by).toHaveLength(300)
    expect(b.completeness.citations).toBe(true)
  })
  it('a capped patent-citations table also reports incomplete', () => {
    expect(extractBrief(body(table('patentCitations', 301)), url).completeness.citations).toBe(false)
  })
  it('over 300 classifications reports classifications incomplete; 300 is complete', () => {
    const page = (n) => body(`<classification-viewer>${Array.from({ length: n }, (_, i) => tree(i)).join('')}</classification-viewer>`)
    const b = extractBrief(page(301), url)
    expect(b.classifications).toHaveLength(300)
    expect(b.completeness.classifications).toBe(false)
    expect(extractBrief(page(300), url).completeness.classifications).toBe(true)
  })
  it('over 300 landscape terms reports landscapes incomplete; 300 is complete', () => {
    const terms = (n) => Array.from({ length: n }, (_, i) => `<landscape-link><state-modifier data-term="term${i}"></state-modifier></landscape-link>`).join('')
    expect(extractBrief(body(terms(301)), url).completeness.landscapes).toBe(false)
    expect(extractBrief(body(terms(300)), url).completeness.landscapes).toBe(true)
  })
  it('over 300 legal events reports legal_events incomplete', () => {
    const rows = (n) => Array.from({ length: n }, (_, i) => `<div class="tr"><span class="td">2001-01-01</span><span class="td">C${i}</span><span class="td">Title ${i}</span></div>`).join('')
    const page = (n) => body(`<h3 id="legalEvents">x</h3><div class="responsive-table"><div class="tbody">${rows(n)}</div></div>`)
    const b = extractBrief(page(301), url)
    expect(b.legal_events).toHaveLength(CAPS.listItems)
    expect(b.completeness.legal_events).toBe(false)
    expect(extractBrief(page(300), url).completeness.legal_events).toBe(true)
  })
  it('over 200 claims reports claims incomplete; 200 is complete', () => {
    const over = extractBrief(claimsPage(201), url)
    expect(over.claims).toHaveLength(CAPS.claims)
    expect(over.completeness.claims).toBe(false)
    expect(extractBrief(claimsPage(200), url).completeness.claims).toBe(true)
  })
  it('a claim cut at the per-claim cap reports claims incomplete', () => {
    const doc = body(`<patent-text name="claims"><section class="flex"><div class="claims"><div num="1" class="claim"><div class="claim-text">1. ${'x'.repeat(CAPS.claimChars + 50)}</div></div></div></section></patent-text>`)
    expect(extractBrief(doc, url).completeness.claims).toBe(false)
  })
  it('an over-long abstract reports abstract incomplete', () => {
    const doc = body(`<patent-text name="abstract"><section class="flex"><div>${'a'.repeat(CAPS.abstract + 10)}</div></section></patent-text>`)
    expect(extractBrief(doc, url).completeness.abstract).toBe(false)
  })
})

describe('fitBrief reports what it shrank', () => {
  const mk = () => ({
    patent_number: 'US1B1',
    dates: {}, inventors: ['i'], assignees: ['a'], landscapes: ['l'],
    classifications: [{ code: 'A', path: ['p'.repeat(300)] }],
    claims: [{ number: 1, text: 'A.', depends_on: [], independent: true }, { number: 2, text: 'B.', depends_on: [1], independent: false }],
    description: [],
    family: Array.from({ length: 100 }, (_, i) => ({ number: `F${i}`, status: 's'.repeat(300) })),
    legal_events: Array.from({ length: 100 }, () => ({ date: '2000-01-01', text: 't'.repeat(300) })),
    citations: { patent: Array.from({ length: 100 }, () => ({ number: 'US1A', title: 't'.repeat(300) })), cited_by: [] },
    completeness: {},
  })

  it('names the shrunk sections in `partial`', () => {
    const { brief, partial } = fitBrief(mk(), 0, 12_000)
    expect(brief.citations.patent.length).toBeLessThan(100)
    expect(partial.has('citations')).toBe(true)
    expect(partial.has('legal_events')).toBe(true)
    expect(partial.has('claims')).toBe(false)
  })
  it('names nothing when nothing shrinks, and claims when one is popped', () => {
    expect(fitBrief(mk(), 0, 10_000_000).partial.size).toBe(0)
    const m = mk()
    m.claims = Array.from({ length: 50 }, (_, i) => ({ number: i + 1, text: 'c'.repeat(2000), depends_on: [], independent: true }))
    m.citations.patent = []
    m.legal_events = []
    m.family = []
    const { brief, partial } = fitBrief(m, 0, 20_000)
    expect(brief.claims.length).toBeLessThan(50)
    expect(partial.has('claims')).toBe(true)
  })
  it('end to end, a size-shrunk extraction reports citations incomplete', () => {
    const big = (id) => `<h3 id="${id}">x</h3><div class="responsive-table"><div class="tbody">${Array.from({ length: 300 }, (_, i) => `<div class="tr"><span class="td"><a>US${i}A</a></span><span class="td">d</span><span class="td">2000-01-01</span><span class="td">${'a'.repeat(400)}</span><span class="td">${'t'.repeat(400)}</span></div>`).join('')}</div></div>`
    const paras = Array.from({ length: 60 }, (_, i) => `<div class="description-paragraph">${String(i % 10).repeat(9500)}</div>`).join('')
    const b = extractBrief(body(`${big('patentCitations')}${big('citedBy')}<patent-text name="description"><section class="flex"><div><heading>H</heading>${paras}</div></section></patent-text>`), url)
    expect(b.citations.patent.length).toBeLessThan(300)
    expect(b.completeness.citations).toBe(false)
  })
  it('the minimal fallback keeps claims complete only when every claim survives intact', () => {
    const { brief, partial } = fitBrief(mk(), 0, 100)
    expect(brief.completeness.claims).toBe(true)
    expect(partial.has('claims')).toBe(false)
    expect(partial.has('citations')).toBe(true)
  })
})

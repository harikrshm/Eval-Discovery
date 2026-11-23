// Round R12 hardening: priority date source, size guarantee and truncation marker,
// timeline kind whitelist, claim numbering edge cases, drift against the committed briefs.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { CAPS, backendSize, extractBrief, fitBrief } from './extractor.js'
import { load } from './testutil.js'

const URLS = {
  'ep4517554a1': 'EP4517554A1',
  'us12401720b1': 'US12401720B1',
  'us6756997b1': 'US6756997B1',
  'us20250119367a1': 'US20250119367A1',
}
const BACKEND_CAP = 600_000

function parse(html) {
  return new DOMParser().parseFromString(html, 'text/html')
}

describe('drift: extractor output equals the committed expected briefs', () => {
  it.each(Object.entries(URLS))('%s', (name, number) => {
    const { doc } = load(`${name}.html`)
    const expected = JSON.parse(readFileSync(resolve(import.meta.dirname, `../fixtures/${name}.expected.json`), 'utf8'))
    expect(extractBrief(doc, `https://patents.google.com/patent/${number}/en`)).toEqual(expected)
  })
})

describe('priority date comes from the Priority Applications table', () => {
  const url = 'https://patents.google.com/patent/US1B1/en'
  const priorityTable = (rows) =>
    '<h3 id="applicationPriorityApps">Priority Applications (2)</h3><div class="responsive-table"><div class="table"><div class="tbody">' +
    rows.map(([app, prio, filing]) => `<div class="tr"><span class="td">${app}</span><span class="td">${prio}</span><span class="td">${filing}</span><span class="td">T</span></div>`).join('') +
    '</div></div></div>'
  const timeline =
    '<application-timeline><div class="event"><div date class="priority">2025-05-08</div><div class="title"><span class="title-text">Priority to US1</span></div></div>' +
    '<div class="event"><div date class="filed">2025-05-08</div><div class="title"><span class="title-text">Application filed</span></div></div></application-timeline>'

  it('takes the earliest valid priority date, not the "Priority to" event date', () => {
    const b = extractBrief(parse(`<body>${timeline}${priorityTable([['US19/1', '2013-02-03', '2025-05-08'], ['US19/2', '2012-01-09', '2025-08-13'], ['US19/3', 'n/a', 'x']])}</body>`), url)
    expect(b.dates.priority).toBe('2012-01-09')
    expect(b.dates.filing).toBe('2025-05-08')
  })
  it('leaves priority unset when the page has no such table (the EP page)', () => {
    const b = extractBrief(parse(`<body>${timeline}</body>`), url)
    expect(b.dates.priority).toBeUndefined()
    expect(b.dates.filing).toBe('2025-05-08')
    const ep = extractBrief(load('ep4517554a1.html').doc, 'https://patents.google.com/patent/EP4517554A1/en')
    expect('priority' in ep.dates).toBe(false)
  })
  it('ignores a table with no valid dates', () => {
    const b = extractBrief(parse(`<body>${priorityTable([['US19/1', 'soon', 'x']])}</body>`), url)
    expect(b.dates.priority).toBeUndefined()
  })
})

describe('timeline kinds are a whitelist', () => {
  const ev = (cls, date, text) =>
    `<div class="event"><div date class="${cls}">${date}</div><div class="title"><span class="title-text">${text}</span></div></div>`
  it('finds the kind whatever the class order, and ignores unknown kinds', () => {
    const doc = parse(
      '<body><application-timeline>' +
        ev('style-scope application-timeline filed', '2020-01-02', 'Application filed by Acme') +
        ev('x-scope legal-status style-scope', 'Status', 'Active') +
        ev('mystery', '2021-02-03', 'Something unknown') +
        ev('granted extra', '2022-03-04', 'Application granted') +
        '</application-timeline></body>',
    )
    const b = extractBrief(doc, 'https://patents.google.com/patent/US1B1/en')
    expect(b.dates).toEqual({ filing: '2020-01-02', grant: '2022-03-04' })
    expect(b.status).toBe('Active')
    expect(b.legal_events.map((e) => e.text)).toEqual(['Application filed by Acme', 'Application granted'])
  })
})

describe('claim numbering edge cases', () => {
  const url = 'https://patents.google.com/patent/US1B1/en'
  const claimsPage = (inner) => parse(`<body><patent-text name="claims"><section class="flex"><div class="claims">${inner}</div></section></patent-text></body>`)
  const claim = (num, text) => `<div class="claim"${num === null ? '' : ` num="${num}"`}><div class="claim-text">${text}</div></div>`

  it('counts on when a claim has no num attribute', () => {
    const b = extractBrief(claimsPage(claim('1', '1. A device.') + claim(null, '2. The device of claim 1.') + claim('', 'The device of claim 2.')), url)
    expect(b.claims.map((c) => [c.number, c.depends_on])).toEqual([[1, []], [2, [1]], [3, [2]]])
    expect(b.claims[1].text).toBe('The device of claim 1.')
  })
  it('strips zero-padded number prefixes consistently', () => {
    const b = extractBrief(claimsPage(claim('00001', '00001. A device.') + claim('00007', '00007. The device of claim 1.') + claim('8', '08) The device of claim 7.')), url)
    expect(b.claims.map((c) => c.text)).toEqual(['A device.', 'The device of claim 1.', 'The device of claim 7.'])
    expect(b.claims.map((c) => c.number)).toEqual([1, 7, 8])
  })
  it('does not strip a longer number that merely starts with the claim number', () => {
    const b = extractBrief(claimsPage(claim('1', '10. widgets are claimed.')), url)
    expect(b.claims[0].text).toBe('10. widgets are claimed.')
  })
  it('skips duplicate and non-positive numbers', () => {
    const b = extractBrief(claimsPage(claim('1', 'A.') + claim('1', 'B duplicate.') + claim('0', 'C zero.') + claim('-3', 'D negative.') + claim('2', 'E.')), url)
    expect(b.claims.map((c) => [c.number, c.text])).toEqual([[1, 'A.'], [2, 'E.']])
  })
  it('skips absurd numbers instead of emitting invalid JSON numbers', () => {
    const b = extractBrief(claimsPage(claim('9'.repeat(40), 'A.') + claim('1', 'B.')), url)
    expect(b.claims.map((c) => c.number)).toEqual([1])
  })
  it('handles the real nested wrappers (wrapper divs are not claims)', () => {
    const b = extractBrief(claimsPage(`<div class="claim"><div class="claim" num="1"><div class="claim-text">1. A.</div></div></div><li class="claim"><div class="claim" num="2"><div class="claim-text">The A of claim 1.</div></div></li>`), url)
    expect(b.claims.map((c) => c.number)).toEqual([1, 2])
  })
})

describe('size guarantee', () => {
  const url = 'https://patents.google.com/patent/US1B1/en'
  const cap = (b) => backendSize(b)

  it('measures the way the backend does (null defaults count)', () => {
    const lean = { patent_number: 'US1B1', dates: {}, inventors: [], assignees: [], classifications: [{ code: 'A' }], landscapes: [], claims: [], description: [{ text: 't' }], citations: { patent: [{ number: 'n' }], cited_by: [] }, family: [], legal_events: [], completeness: {} }
    expect(cap(lean)).toBeGreaterThan(JSON.stringify(lean).length)
  })

  it('drops citation rows before description sections, and marks the truncation', () => {
    const rows = Array.from({ length: 300 }, (_, i) => `<div class="tr"><span class="td"><a>US${i}A</a></span><span class="td">d</span><span class="td">2000-01-01</span><span class="td">${'a'.repeat(400)}</span><span class="td">${'t'.repeat(400)}</span></div>`).join('')
    const table = (id) => `<h3 id="${id}">x</h3><div class="responsive-table"><div class="tbody">${rows}</div></div>`
    const paras = Array.from({ length: 60 }, (_, i) => `<div class="description-paragraph">${String(i % 10).repeat(9500)}</div>`).join('')
    const doc = parse(`<body><h1 id="title">T</h1>${table('patentCitations')}${table('citedBy')}<patent-text name="description"><section class="flex"><div><heading>H</heading>${paras}</div></section></patent-text></body>`)
    const b = extractBrief(doc, url)
    expect(backendSize(b)).toBeLessThanOrEqual(CAPS.total)
    expect(CAPS.total).toBeLessThanOrEqual(BACKEND_CAP - 60_000)
    // citations were cut down before any description section was dropped
    expect(b.citations.patent.length).toBeLessThan(300)
    expect(b.description.length).toBeGreaterThan(5)
    const last = b.description.at(-1)
    if (last.heading === '[description truncated]') {
      expect(last.text).toMatch(/^\d+ further sections omitted to fit the size limit$/)
      expect(b.completeness.description).toBe(false)
    }
    expect(b.description.length).toBeLessThanOrEqual(CAPS.descriptionSections)
  })

  it('fitBrief shrinks in order: citations first, then description from the end with a counted marker', () => {
    const mk = () => ({
      patent_number: 'US1B1',
      dates: {}, inventors: [], assignees: [], landscapes: [], family: [], legal_events: [],
      classifications: [{ code: 'A', path: ['p'.repeat(300)] }],
      claims: [{ number: 1, text: 'A.', depends_on: [], independent: true }],
      description: Array.from({ length: 15 }, (_, i) => ({ heading: `H${i}`, text: 'x'.repeat(20000) })),
      citations: {
        patent: Array.from({ length: 300 }, () => ({ number: 'US1A', title: 't'.repeat(300) })),
        cited_by: Array.from({ length: 300 }, () => ({ number: 'US2A', title: 't'.repeat(300) })),
      },
      completeness: {},
    })
    // Dropping the citation rows alone is enough: every description section survives, no marker.
    const a = fitBrief(mk(), 0, 330_000)
    expect(a.brief.description).toHaveLength(15)
    expect(a.omitted).toBe(0)
    expect(a.brief.citations.patent.length + a.brief.citations.cited_by.length).toBeLessThan(600)
    expect(backendSize(a.brief)).toBeLessThanOrEqual(330_000)
    // A smaller budget also cuts description sections from the end and counts them in the marker.
    const b = fitBrief(mk(), 0, 150_000)
    expect(b.brief.citations.patent).toHaveLength(0)
    expect(b.brief.citations.cited_by).toHaveLength(0)
    expect(b.omitted).toBeGreaterThan(0)
    const kept = b.brief.description.slice(0, -1)
    expect(kept.map((d) => d.heading)).toEqual(Array.from({ length: kept.length }, (_, i) => `H${i}`))
    expect(b.brief.description.at(-1)).toEqual({
      heading: '[description truncated]',
      text: `${15 - kept.length} further sections omitted to fit the size limit`,
    })
    expect(backendSize(b.brief)).toBeLessThanOrEqual(150_000)
    expect(b.brief.claims).toHaveLength(1)
  })

  it('marks sections lost to the section cap too', () => {
    // 45 headings, each a small section: more than 40 sections
    const parts = Array.from({ length: 45 }, (_, i) => `<heading>H${i}</heading><div class="description-paragraph">p${i}</div>`).join('')
    const b = extractBrief(parse(`<body><patent-text name="description"><section class="flex"><div>${parts}</div></section></patent-text></body>`), url)
    expect(b.description).toHaveLength(CAPS.descriptionSections)
    expect(b.description.at(-1)).toEqual({ heading: '[description truncated]', text: '6 further sections omitted to fit the size limit' })
    expect(b.description[0].heading).toBe('H0')
    expect(b.completeness.description).toBe(false)
  })

  it('an untruncated description keeps completeness true and has no marker', () => {
    const { doc } = load('us12401720b1.html')
    const b = extractBrief(doc, 'https://patents.google.com/patent/US12401720B1/en')
    expect(b.completeness.description).toBe(true)
    expect(b.description.some((d) => d.heading === '[description truncated]')).toBe(false)
  })

  it('300 classifications x 8 levels x 400-char descriptions still fit (paths are dropped)', () => {
    const tree = (n) => {
      const levels = Array.from({ length: 8 }, (_, l) => `<concept-mention><state-modifier class="code" data-cpc="C${n}L${l}"></state-modifier><span class="description">${'d'.repeat(400)}</span></concept-mention>`).join('')
      return `<classification-tree>${levels}</classification-tree>`
    }
    const doc = parse(`<body><h1 id="title">T</h1><classification-viewer>${Array.from({ length: 300 }, (_, i) => tree(i)).join('')}</classification-viewer></body>`)
    const b = extractBrief(doc, url)
    expect(b.classifications.length).toBeGreaterThan(0)
    expect(backendSize(b)).toBeLessThanOrEqual(BACKEND_CAP - 60_000)
    expect(b.classifications.every((c) => c.code.length <= 500 && (c.title || '').length <= 500)).toBe(true)
  })

  it('falls back to a minimal valid brief when nothing fits', () => {
    const { doc } = load('us12401720b1.html')
    const full = extractBrief(doc, 'https://patents.google.com/patent/US12401720B1/en')
    const { brief } = fitBrief(structuredClone(full), 0, 1000)
    expect(Object.keys(brief).sort()).toEqual(['assignees', 'citations', 'claims', 'classifications', 'completeness', 'dates', 'description', 'family', 'inventors', 'landscapes', 'legal_events', 'patent_number', 'title'].sort())
    expect(brief.patent_number).toBe('US12401720B1')
    expect(brief.title).toBe(full.title)
    expect(brief.claims.length).toBe(5)
    expect(brief.description).toEqual([])
    // I1: only the first 5 of the claims survive, so claims are reported incomplete too
    expect(Object.values(brief.completeness).every((v) => v === false)).toBe(true)
    // claims keep their dependency data
    expect(brief.claims[1]).toMatchObject({ number: 2, depends_on: [1], independent: false })
  })
})

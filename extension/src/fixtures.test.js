// Assertions on the four committed trimmed real pages. Every expected value
// was checked by hand against the saved raw page (see the task report); the
// trimmed fixtures keep the first 30 rows of each citation table, the first 40
// classification trees and the first paragraphs of each description section.
import { describe, expect, it } from 'vitest'
import { isUsPatentNumber } from './claimrefs.js'
import { CAPS, extractBrief } from './extractor.js'
import { load } from './testutil.js'

const PAGES = {
  ep: ['ep4517554a1.html', 'EP4517554A1'],
  us12: ['us12401720b1.html', 'US12401720B1'],
  us67: ['us6756997b1.html', 'US6756997B1'],
  us20: ['us20250119367a1.html', 'US20250119367A1'],
}

function brief(key) {
  const [file, number] = PAGES[key]
  const { doc } = load(file)
  return extractBrief(doc, `https://patents.google.com/patent/${number}/en`)
}

function claim(b, n) {
  return b.claims.find((c) => c.number === n)
}

describe('EP application EP4517554A1 (non-US)', () => {
  const b = brief('ep')
  it('reads the bibliographic data', () => {
    expect(b.patent_number).toBe('EP4517554A1')
    expect(b.title).toBe('Confidence generation for managing a generative artificial intelligence model')
    expect(b.inventors).toEqual(['Dusan Bosnjakovic', 'Anshuman SAHU'])
    expect(b.assignees).toEqual(['Intuit Inc'])
    expect(b.status).toBe('Pending')
    expect(b.dates).toEqual({ filing: '2024-07-12', publication: '2025-03-05' })
    expect(b.abstract.startsWith('Systems and methods are disclosed for managing a generative artificial intelligence (AI) model.')).toBe(true)
  })
  it('is not a US number', () => {
    expect(b.patent_number.startsWith('EP')).toBe(true)
    expect(isUsPatentNumber(b.patent_number)).toBe(false)
  })
  it('reads claims without number prefixes (EP markup is an ol/li)', () => {
    expect(b.claims).toHaveLength(12)
    expect(b.claims.filter((c) => c.independent).map((c) => c.number)).toEqual([1, 7])
    expect(claim(b, 1).text.startsWith('A computer-implemented method for managing a generative artificial intelligence (AI) model')).toBe(true)
    expect(claim(b, 5).depends_on).toEqual([3, 4]) // "claim 3 or claim 4"
    expect(claim(b, 12).depends_on).toEqual([11])
  })
  it('reads classifications, landscapes, citations, family and legal events', () => {
    expect(b.classifications.map((c) => c.code)).toEqual([
      'G06F40/35', 'G06F40/40', 'G06F16/3329', 'G06N3/042', 'G06N3/0475', 'G06N3/09', 'G06N5/022', 'G06N3/0455',
    ])
    expect(b.classifications[0]).toMatchObject({ code: 'G06F40/35', title: 'Discourse or dialogue representation' })
    expect(b.classifications[0].path.map((p) => p.split(' ')[0])).toEqual(['G', 'G06', 'G06F', 'G06F40/00', 'G06F40/30'])
    expect(b.landscapes).toEqual(['Engineering & Computer Science', 'Theoretical Computer Science'])
    expect(b.citations.patent).toHaveLength(12) // page heading: "Patent Citations (12)"
    expect(b.citations.patent[0]).toEqual({
      number: 'US8051104B2', date: '2011-11-01', assignee: 'Google Inc.', title: 'Editing a network of interconnected concepts',
    })
    expect(b.citations.cited_by).toHaveLength(30) // trimmed: the page lists 60
    expect(b.family).toHaveLength(4)
    expect(b.family[0]).toEqual({ number: 'US11875130B1', country: 'US', status: 'Active' })
    expect(b.legal_events).toHaveLength(7)
    expect(b.legal_events[0]).toEqual({ date: '2024-07-12', text: 'Application filed by Intuit Inc' })
    expect(b.legal_events.some((e) => /^Application granted/.test(e.text))).toBe(false)
    expect(b.dates.grant).toBeUndefined()
  })
  it('splits the description by heading', () => {
    expect(b.description.map((d) => d.heading)).toEqual([
      'CROSS-REFERENCE TO RELATED APPLICATION', 'TECHNICAL FIELD', 'DESCRIPTION OF RELATED ART',
      'SUMMARY', 'BRIEF DESCRIPTION OF THE DRAWINGS', 'DETAILED DESCRIPTION',
    ])
    expect(b.description[0].text).toContain('This Patent Application claims priority to')
    expect(b.description[0].text).not.toContain('[0001]') // paragraph numbers are not text
  })
  it('marks every section complete', () => {
    expect(Object.values(b.completeness).every((v) => v === true)).toBe(true)
  })
})

describe('US grant US12401720B1 (recent)', () => {
  const b = brief('us12')
  it('reads the bibliographic data', () => {
    expect(b.patent_number).toBe('US12401720B1')
    expect(isUsPatentNumber(b.patent_number)).toBe(true)
    expect(b.title).toBe('System and method for server based control')
    expect(b.inventors).toEqual(['Yehuda Binder', 'Benjamin Maytal'])
    expect(b.assignees).toEqual(['Smart Speaker LLC'])
    expect(b.status).toBe('Active')
    expect(b.expiry).toBe('2033-01-03')
    expect(b.dates).toEqual({ priority: '2025-05-08', filing: '2025-05-08', publication: '2025-08-26', grant: '2025-08-26' })
    expect(b.abstract.startsWith('A system and method in a building or vehicle for an actuator operation in response to a sensor')).toBe(true)
  })
  it('reads independent and dependent claims with depends_on', () => {
    expect(b.claims).toHaveLength(29)
    expect(b.claims.filter((c) => c.independent).map((c) => c.number)).toEqual([1])
    expect(claim(b, 1)).toMatchObject({ depends_on: [], independent: true })
    expect(claim(b, 1).text.startsWith('A device comprising: multiple microphones for capturing first human voice data;')).toBe(true)
    expect(claim(b, 1).text).toContain('wherein the device is addressed in the WLAN')
    expect(claim(b, 5)).toMatchObject({ depends_on: [4], independent: false })
    expect(claim(b, 29)).toMatchObject({ depends_on: [23], independent: false })
    expect(claim(b, 2).text.startsWith('The device according to claim 1,')).toBe(true) // number prefix stripped
  })
  it('reads classifications and landscapes', () => {
    expect(b.classifications).toHaveLength(16)
    expect(b.classifications[0].code).toBe('H04L67/12')
    expect(b.classifications[0].title).toMatch(/^Protocols specially adapted for proprietary or special-purpose networking environments/)
    expect(b.classifications.map((c) => c.code)).toContain('Y04S40/18')
    expect(b.landscapes).toEqual(['Engineering & Computer Science', 'Medical Informatics'])
  })
  it('reads citations (trimmed fixture keeps 30 of 558 / 969)', () => {
    expect(b.citations.patent).toHaveLength(30)
    expect(b.citations.patent[0]).toEqual({
      number: 'US3184842A', date: '1965-05-25', assignee: 'Aeroprojects Inc', title: 'Method and apparatus for delivering vibratory energy',
    })
    expect(b.citations.cited_by).toHaveLength(30)
    expect(b.citations.cited_by[0]).toMatchObject({ number: 'US7495558B2', date: '2009-02-24', assignee: 'Infratab, Inc.' })
  })
  it('reads family rows and legal events including the grant', () => {
    expect(b.family).toHaveLength(47)
    expect(b.family[0]).toEqual({ number: 'US20130201316A1', country: 'US', status: 'Abandoned' })
    expect(b.legal_events).toHaveLength(11)
    expect(b.legal_events).toContainEqual({ date: '2025-08-26', text: 'Application granted' })
    expect(b.legal_events).toContainEqual({ date: '2025-08-13', text: 'STCF Information on status: patent grant - Free format text: PATENTED CASE' })
  })
  it('reads description sections', () => {
    expect(b.description.map((d) => d.heading)).toEqual([
      'TECHNICAL FIELD', 'BACKGROUND', 'SUMMARY', 'BRIEF DESCRIPTION OF THE DRAWINGS', 'DETAILED DESCRIPTION',
    ])
    expect(b.description[0].text).toBe('This disclosure relates generally to an apparatus and method for control such as in a building or in a vehicle using a server implementing gateway or control functionalities.')
  })
})

describe('US grant US6756997B1 (older)', () => {
  const b = brief('us67')
  it('reads the bibliographic data', () => {
    expect(b.patent_number).toBe('US6756997B1')
    expect(b.title).toBe('Systems and methods for displaying and recording control interface with television programs, video, advertising information and program scheduling information')
    expect(b.inventors).toEqual(['Thomas E. Ward, III', 'Douglas B. Macrae'])
    expect(b.assignees).toEqual(['Gemstar Development LLC'])
    expect(b.status).toBe('Expired - Lifetime')
    expect(b.expiry).toBe('2019-09-05')
    expect(b.dates).toEqual({ priority: '2000-06-14', filing: '2000-06-14', publication: '2004-06-29', grant: '2004-06-29' })
    expect(b.abstract).toContain('“EPG”') // curly quotes survive
  })
  it('reads two independent claims and their dependents', () => {
    expect(b.claims).toHaveLength(38)
    expect(b.claims.filter((c) => c.independent).map((c) => c.number)).toEqual([1, 17])
    expect(claim(b, 1).text.startsWith('A method for navigating about an on screen television interactive program guide comprising:')).toBe(true)
    expect(claim(b, 2)).toMatchObject({ depends_on: [1], independent: false })
    expect(claim(b, 8)).toMatchObject({ depends_on: [7], independent: false })
    expect(claim(b, 38)).toMatchObject({ depends_on: [36], independent: false })
  })
  it('reads classifications, citations, family and legal events', () => {
    expect(b.classifications).toHaveLength(25)
    expect(b.classifications[0]).toMatchObject({ code: 'H04N21/42204' })
    expect(b.landscapes).toEqual(['Engineering & Computer Science', 'Multimedia'])
    expect(b.citations.patent).toHaveLength(30) // page: Patent Citations (122); trimmed
    expect(b.citations.patent[0].number).toBe('US4439784A')
    expect(b.family.map((f) => f.number).slice(0, 2)).toEqual(['US6177931B1', 'US6756997B1'])
    expect(b.family).toHaveLength(3)
    expect(b.legal_events).toHaveLength(15)
    expect(b.legal_events).toContainEqual({ date: '2004-06-29', text: 'Application granted' })
    expect(b.legal_events).toContainEqual({ date: '2007-02-20', text: 'CC Certificate of correction' })
  })
})

describe('US application US20250119367A1', () => {
  const b = brief('us20')
  it('reads the bibliographic data (no grant)', () => {
    expect(b.patent_number).toBe('US20250119367A1')
    expect(isUsPatentNumber(b.patent_number)).toBe(true)
    expect(b.title).toBe('Unique id generation for sensors')
    expect(b.assignees).toEqual(['Cisco Technology Inc'])
    expect(b.inventors).toHaveLength(5)
    expect(b.status).toBe('Pending')
    expect(b.expiry).toBeUndefined()
    expect(b.dates).toEqual({ priority: '2024-12-16', filing: '2024-12-16', publication: '2025-04-10' })
    expect(b.legal_events.some((e) => /^Application granted/.test(e.text))).toBe(false)
  })
  it('reads two independent claims', () => {
    expect(b.claims).toHaveLength(20)
    expect(b.claims.filter((c) => c.independent).map((c) => c.number)).toEqual([1, 11])
    // the page renders the number as <b>1</b>. and the prefix is stripped
    expect(claim(b, 1).text.startsWith('A method comprising: receiving, over a network, at least one attribute associated with a sensor;')).toBe(true)
    expect(claim(b, 14)).toMatchObject({ depends_on: [13], independent: false })
    expect(claim(b, 20)).toMatchObject({ depends_on: [11], independent: false })
  })
  it('reads classifications (first 40 trees kept in the fixture)', () => {
    expect(b.classifications).toHaveLength(40)
    expect(b.classifications[0].code).toBe('G06F9/45558')
    expect(b.classifications[0].path[0]).toBe('G PHYSICS')
    expect(b.landscapes).toEqual(['Engineering & Computer Science', 'Computer Networks & Wireless Communication'])
  })
  it('reads the description headings and legal events', () => {
    expect(b.description.map((d) => d.heading).slice(0, 3)).toEqual(['CROSS-REFERENCE TO RELATED APPLICATIONS', 'TECHNICAL FIELD', 'BACKGROUND'])
    expect(b.description.map((d) => d.heading)).toContain('Overview')
    expect(b.legal_events).toHaveLength(9)
    expect(b.legal_events[0]).toEqual({ date: '2024-12-16', text: 'Application filed by Cisco Technology Inc' })
  })
})

describe('missing sections', () => {
  const byName = (d, name) => [...d.querySelectorAll('patent-text')].filter((e) => e.getAttribute('name') === name)
  const REMOVALS = {
    abstract: (d) => byName(d, 'abstract').forEach((e) => e.remove()),
    claims: (d) => byName(d, 'claims').forEach((e) => e.remove()),
    description: (d) => byName(d, 'description').forEach((e) => e.remove()),
    classifications: (d) => d.querySelectorAll('classification-viewer').forEach((e) => e.remove()),
    landscapes: (d) => d.querySelectorAll('landscape-link').forEach((e) => e.remove()),
    citations: (d) => d.querySelectorAll('h3').forEach((h) => ['patentCitations', 'citedBy'].includes(h.id) && h.nextElementSibling.remove()),
    family: (d) => d.querySelectorAll('application-timeline .block').forEach((e) => e.remove()),
    legal_events: (d) => {
      d.querySelectorAll('h3').forEach((h) => h.id === 'legalEvents' && h.nextElementSibling.remove())
      d.querySelectorAll('application-timeline .event').forEach((e) => e.remove())
    },
    bibliographic: (d) => {
      d.querySelectorAll('h1').forEach((e) => e.remove())
      d.querySelectorAll('dl.important-people').forEach((e) => e.remove())
      d.querySelectorAll('application-timeline').forEach((e) => e.remove())
      d.querySelectorAll('meta').forEach((e) => e.remove())
    },
  }
  it.each(Object.keys(REMOVALS))('removing %s gives completeness false and no exception', (section) => {
    const { doc } = load('us12401720b1.html')
    REMOVALS[section](doc)
    const b = extractBrief(doc, 'https://patents.google.com/patent/US12401720B1/en')
    expect(b.completeness[section]).toBe(false)
    expect(b.patent_number).toBe('US12401720B1')
    // unrelated sections stay found (family and legal events share the timeline element)
    for (const other of ['abstract', 'claims', 'description', 'classifications', 'landscapes', 'citations']) {
      if (other !== section) expect(b.completeness[other]).toBe(true)
    }
  })
  it('grant date falls back to the "Application granted" legal event', () => {
    const { doc } = load('us12401720b1.html')
    doc.querySelectorAll('meta[name="DC.date"]').forEach((m) => m.remove())
    doc.querySelectorAll('application-timeline .granted').forEach((e) => e.classList.remove('granted'))
    const b = extractBrief(doc, 'https://patents.google.com/patent/US12401720B1/en')
    expect(b.dates.grant).toBe('2025-08-26')
  })
})

describe('caps', () => {
  const page = (claims, paras, title = 'T') => new DOMParser().parseFromString(
    `<body><h1 id="title">${title}</h1>` +
    `<patent-text name="claims"><section class="flex"><div class="claims">${claims}</div></section></patent-text>` +
    `<patent-text name="description"><section class="flex"><div><heading>H</heading>${paras}</div></section></patent-text></body>`,
    'text/html',
  )
  const claimDiv = (n, len) => `<div num="${n}" class="claim"><div class="claim-text">${n}. ${'x'.repeat(len)}</div></div>`
  const url = 'https://patents.google.com/patent/US1B1/en'

  it('truncates each over-cap value', () => {
    const paras = Array.from({ length: 80 }, () => `<div class="description-paragraph">${'y'.repeat(9000)}</div>`).join('')
    const doc = page(claimDiv(1, 9000) + claimDiv(2, 10), paras, 't'.repeat(900))
    const b = extractBrief(doc, url)
    expect(b.title).toHaveLength(CAPS.short)
    expect(claim(b, 1).text).toHaveLength(CAPS.claimChars)
    expect(claim(b, 2).text).toBe('x'.repeat(10))
    expect(b.description.length).toBeLessThanOrEqual(CAPS.descriptionSections)
    expect(b.description.length).toBeGreaterThan(20) // long sections are split into parts, not cut
    expect(b.description.every((d) => d.text.length <= CAPS.sectionChars)).toBe(true)
  })
  it('keeps at most 200 claims', () => {
    const doc = page(Array.from({ length: 230 }, (_, i) => claimDiv(i + 1, 100)).join(''), '')
    expect(extractBrief(doc, url).claims).toHaveLength(CAPS.claims)
  })
  it('stays under the total size cap by dropping the description first', () => {
    const claims = Array.from({ length: 100 }, (_, i) => claimDiv(i + 1, 5000)).join('')
    const paras = Array.from({ length: 120 }, () => `<div class="description-paragraph">${'y'.repeat(9000)}</div>`).join('')
    const b = extractBrief(page(claims, paras), url)
    expect(JSON.stringify(b).length).toBeLessThanOrEqual(CAPS.total)
    expect(b.claims).toHaveLength(100)
    expect(b.description.length).toBeLessThan(40)
  })
})

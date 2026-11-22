// Pure extractor: Google Patents page DOM -> Brief-shaped object (see
// backend/core/assist/brief.py for the schema and caps).
//
// Google Patents is a Polymer single-page app that renders into the light DOM
// (no shadow roots), so everything is reachable with ordinary selectors. There
// is no microdata (no itemprop); the structure is custom elements and classes:
//   h1#title, h2#pubnum, dl.important-people (inventor / assignee dt+dd),
//   application-timeline (.event > div[date].{priority|filed|publication|granted|
//   legal-status|reassignment|litigation} + .title-text; ".block" per family member),
//   patent-text[name=abstract|description|claims], classification-viewer >
//   classification-tree > concept-mention (.code[data-cpc] + .description),
//   landscape-link > state-modifier[data-term], and h3#patentCitations /
//   #citedBy / #legalEvents each followed by a .responsive-table.
// NB: ids such as "text" repeat on the page, so lookups avoid "#id" selectors
// (jsdom resolves a duplicated id to its first element only).
//
// Never throws: every section is read inside its own guard and a missing section
// just leaves the field empty and its completeness flag false. Caps are enforced
// by truncation so the result always validates against the backend model.
import { buildClaimTree, normalizePatentNumber } from './claimrefs.js'

export const CAPS = {
  claims: 200,
  claimChars: 8000,
  descriptionSections: 40,
  sectionChars: 20000,
  listItems: 300,
  short: 500,
  abstract: 20000,
  total: 540000, // backend cap is 600000 serialized characters (it counts null defaults); keep headroom
}

const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEMPLATE', 'SVG', 'NOSCRIPT', 'IRON-ICON', 'OVERLAY-TOOLTIP'])
const BLOCK_TAGS = new Set([
  'DIV', 'P', 'LI', 'UL', 'OL', 'BR', 'HEADING', 'CLAIM-STATEMENT', 'SECTION', 'TR', 'TD', 'TH',
  'H1', 'H2', 'H3', 'H4', 'DD', 'DT', 'DL', 'DESCRIPTION-OF-DRAWINGS', 'PARA-NUM',
])

const COMPLETENESS_KEYS = [
  'bibliographic', 'abstract', 'claims', 'description', 'classifications',
  'landscapes', 'citations', 'family', 'legal_events',
]

// ---- small helpers --------------------------------------------------------

function safe(fn, fallback) {
  try {
    const v = fn()
    return v === undefined ? fallback : v
  } catch {
    return fallback
  }
}

function clean(s) {
  return String(s ?? '').replace(/\s+/g, ' ').trim()
}

/** Cut to at most `n` UTF-16 units without splitting a surrogate pair. */
function cut(s, n) {
  if (s.length <= n) return s
  let end = n
  const c = s.charCodeAt(end - 1)
  if (c >= 0xd800 && c <= 0xdbff) end -= 1
  return s.slice(0, end)
}

function short(s) {
  const v = cut(clean(s), CAPS.short)
  return v || undefined
}

function textOf(el) {
  const parts = []
  const walk = (node) => {
    if (node.nodeType === 3) {
      parts.push(node.nodeValue)
    } else if (node.nodeType === 1) {
      if (SKIP_TAGS.has(node.tagName.toUpperCase())) return
      const block = BLOCK_TAGS.has(node.tagName.toUpperCase())
      if (block) parts.push(' ')
      for (const c of node.childNodes) walk(c)
      if (block) parts.push(' ')
    }
  }
  if (el) walk(el)
  return clean(parts.join(''))
}

function all(root, selector) {
  return root && root.querySelectorAll ? [...root.querySelectorAll(selector)] : []
}

function first(root, selector) {
  return root && root.querySelector ? root.querySelector(selector) : null
}

/** Element by tag and id without an "#id" selector (ids repeat on the page). */
function byId(doc, tag, id) {
  return all(doc, tag).find((e) => e.id === id) || null
}

function patentText(doc, name) {
  return all(doc, 'patent-text').find((e) => e.getAttribute('name') === name) || null
}

function uniq(list) {
  return [...new Set(list)]
}

// ---- number ---------------------------------------------------------------

function numberFromUrl(url) {
  if (typeof url !== 'string') return ''
  const m = /\/patent\/([^/?#]+)/.exec(url)
  if (!m) return ''
  let seg = m[1]
  try {
    seg = decodeURIComponent(seg)
  } catch {
    /* keep the raw segment */
  }
  const n = normalizePatentNumber(seg)
  return /^[A-Z]{2}[0-9A-Z]+$/.test(n) ? n : ''
}

function numberFromPage(doc) {
  const h2 = byId(doc, 'h2', 'pubnum')
  if (h2) {
    const n = normalizePatentNumber(textOf(h2))
    if (/^[A-Z]{2}[0-9A-Z]+$/.test(n)) return n
  }
  const meta = (name) => first(doc, `meta[name="${name}"]`)?.getAttribute('content') || ''
  const pub = meta('citation_patent_publication_number') || meta('citation_patent_number')
  if (pub) {
    // "EP:4517554:A1" / "US:12401720" -> country + number + kind
    const n = normalizePatentNumber(pub.split(':').join(''))
    if (/^[A-Z]{2}[0-9A-Z]+$/.test(n)) return n
  }
  const t = /^([A-Z]{2}[0-9]{4,}[A-Z]?[0-9]?)\b/.exec(clean(doc?.title))
  return t ? t[1] : ''
}

// ---- bibliographic --------------------------------------------------------

function readTitle(doc) {
  const h1 = byId(doc, 'h1', 'title')
  if (h1) {
    const t = clean(h1.textContent)
    if (t) return t
  }
  const dc = first(doc, 'meta[name="DC.title"]')?.getAttribute('content')
  if (dc && clean(dc)) return clean(dc)
  const m = /^\S+ - (.+?) - Google Patents$/.exec(clean(doc?.title))
  return m ? m[1] : ''
}

function readPeople(doc) {
  const inventors = []
  const assignees = []
  const dl = first(doc, 'dl.important-people')
  let kind = null
  for (const el of dl ? [...dl.children] : []) {
    const tag = el.tagName
    if (tag === 'DT') {
      const label = clean(el.textContent).toLowerCase()
      kind = label.includes('inventor') ? 'inv' : label.includes('assignee') ? 'asg' : null
    } else if (tag === 'DD' && kind) {
      const name = clean(first(el, '[data-inventor]')?.getAttribute('data-inventor') || first(el, '[data-assignee]')?.getAttribute('data-assignee') || el.textContent)
      if (name) (kind === 'inv' ? inventors : assignees).push(name)
    }
  }
  return { inventors: uniq(inventors), assignees: uniq(assignees) }
}

/** Timeline events: [{kind, date, text, result}] from application-timeline. */
function readTimeline(doc) {
  const tl = first(doc, 'application-timeline')
  const events = []
  for (const ev of all(tl, '.event')) {
    if (ev.querySelector('.block') || ev.classList.contains('expand')) continue
    const dateEl = [...ev.children].find((c) => c.hasAttribute('date'))
    const titleEl = first(ev, '.title')
    if (!dateEl || !titleEl) continue
    const kind = [...dateEl.classList].find((c) => c !== 'style-scope' && c !== 'x-scope') || ''
    const titleText = first(titleEl, '.title-text')
    events.push({
      kind,
      date: clean(dateEl.textContent),
      text: clean(titleText ? titleText.textContent : textOf(titleEl)),
      result: first(titleEl, '[data-result]')?.getAttribute('data-result') || '',
    })
  }
  return events
}

const ISO_DATE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/

function readDates(doc, events, number) {
  const dates = {}
  const pick = (kind, pred) => events.find((e) => e.kind === kind && ISO_DATE.test(e.date) && (!pred || pred(e)))
  const priority = pick('priority')
  const filed = pick('filed')
  const pubs = events.filter((e) => e.kind === 'publication' && ISO_DATE.test(e.date))
  const ownPub = pubs.find((e) => e.result && e.result.split('/')[1] === number) || pubs[0]
  const granted = events.find((e) => e.kind === 'granted' && ISO_DATE.test(e.date)) ||
    events.find((e) => /application granted/i.test(e.text) && ISO_DATE.test(e.date))
  const meta = (scheme) => {
    const m = all(doc, 'meta[name="DC.date"]').find((x) => (x.getAttribute('scheme') || '') === scheme)
    const v = m ? clean(m.getAttribute('content')) : ''
    return ISO_DATE.test(v) ? v : ''
  }
  const set = (key, value) => {
    if (value) dates[key] = value
  }
  set('priority', priority?.date)
  set('filing', filed?.date || meta('dateSubmitted'))
  set('publication', ownPub?.date || meta(''))
  set('grant', granted?.date || meta('issue'))
  return dates
}

function readStatus(events) {
  const status = events.find((e) => e.kind === 'legal-status' && e.date === 'Status')
  const expiry = events.find((e) => e.kind === 'legal-status' && ISO_DATE.test(e.date) && /expir/i.test(e.text))
  return { status: status?.text, expiry: expiry?.date }
}

// ---- classifications, landscapes -----------------------------------------

function readClassifications(doc) {
  const out = []
  const seen = new Set()
  for (const tree of all(first(doc, 'classification-viewer'), 'classification-tree')) {
    const nodes = all(tree, 'concept-mention')
      .map((cm) => ({
        code: clean(first(cm, '[data-cpc]')?.getAttribute('data-cpc') || first(cm, '.code')?.textContent),
        title: clean(first(cm, '.description')?.textContent),
      }))
      .filter((n) => n.code)
    if (!nodes.length) continue
    const leaf = nodes[nodes.length - 1]
    if (seen.has(leaf.code)) continue
    seen.add(leaf.code)
    const item = { code: cut(leaf.code, CAPS.short) }
    const title = short(leaf.title)
    if (title) item.title = title
    item.path = nodes.slice(0, -1).slice(0, CAPS.listItems).map((n) => cut(clean(`${n.code} ${n.title}`), CAPS.short))
    out.push(item)
    if (out.length >= CAPS.listItems) break
  }
  return out
}

function readLandscapes(doc) {
  const terms = all(doc, 'landscape-link [data-term]').map((e) => clean(e.getAttribute('data-term'))).filter(Boolean)
  return uniq(terms).slice(0, CAPS.listItems).map((t) => cut(t, CAPS.short))
}

// ---- text sections --------------------------------------------------------

function readAbstract(doc) {
  const pt = patentText(doc, 'abstract')
  const sec = first(pt, 'section.flex') || pt
  const t = textOf(sec)
  return t ? cut(t, CAPS.abstract) : ''
}

function chunkParagraphs(paragraphs, limit) {
  const chunks = []
  let cur = ''
  for (const p of paragraphs) {
    const piece = cut(p, limit)
    if (cur && cur.length + 2 + piece.length > limit) {
      chunks.push(cur)
      cur = piece
    } else {
      cur = cur ? `${cur}\n\n${piece}` : piece
    }
  }
  if (cur) chunks.push(cur)
  return chunks
}

function readDescription(doc) {
  const pt = patentText(doc, 'description')
  if (!pt) return []
  const sections = []
  let current = { heading: null, paragraphs: [] }
  const flush = () => {
    if (current.paragraphs.length) sections.push(current)
  }
  for (const el of all(pt, 'heading, .description-paragraph, .description-line')) {
    if (el.tagName === 'HEADING') {
      flush()
      current = { heading: clean(el.textContent), paragraphs: [] }
    } else {
      const t = textOf(el)
      if (t) current.paragraphs.push(t)
    }
  }
  flush()
  const out = []
  for (const s of sections) {
    const chunks = chunkParagraphs(s.paragraphs, CAPS.sectionChars)
    chunks.forEach((text, i) => {
      const section = { text }
      if (s.heading) section.heading = cut(i === 0 ? s.heading : `${s.heading} (part ${i + 1})`, CAPS.short)
      out.push(section)
    })
  }
  return out.slice(0, CAPS.descriptionSections)
}

function readClaims(doc) {
  const pt = patentText(doc, 'claims')
  const raw = []
  const seen = new Set()
  for (const el of all(pt, '[num]')) {
    if (!el.classList.contains('claim')) continue
    const number = parseInt(el.getAttribute('num'), 10)
    if (!Number.isInteger(number) || number < 1 || seen.has(number)) continue
    seen.add(number)
    let text = textOf(el)
    // The page prefixes the claim's own number ("1. A device ..."); EP pages do not.
    text = text.replace(new RegExp(`^${number}\\s*[.)]\\s*`), '')
    raw.push({ number, text: cut(text, CAPS.claimChars) })
    if (raw.length >= CAPS.claims) break
  }
  return buildClaimTree(raw)
}

// ---- tables ---------------------------------------------------------------

function tableAfter(doc, headingId) {
  const h3 = byId(doc, 'h3', headingId)
  let el = h3 ? h3.nextElementSibling : null
  while (el && !el.classList.contains('responsive-table')) {
    if (el.tagName === 'H3') return null
    el = el.nextElementSibling
  }
  return el
}

function rowsOf(table) {
  return all(table, '.tbody > .tr').filter((r) => !first(r, '.famheader'))
}

function cells(row) {
  return [...row.children].filter((c) => c.classList.contains('td'))
}

function readCitations(doc, headingId) {
  const rows = []
  for (const row of rowsOf(tableAfter(doc, headingId))) {
    const c = cells(row)
    if (c.length < 3) continue
    const link = first(c[0], 'a')
    const item = {}
    const number = short(link ? link.textContent : c[0].textContent.replace(/[*†‡]/g, ''))
    if (number) item.number = number
    const date = short(c[2].textContent)
    if (date) item.date = date
    if (c[3]) {
      const assignee = short(c[3].textContent)
      if (assignee) item.assignee = assignee
    }
    if (c[4]) {
      const title = short(c[4].textContent)
      if (title) item.title = title
    }
    if (Object.keys(item).length) rows.push(item)
    if (rows.length >= CAPS.listItems) break
  }
  return rows
}

function readLegalEvents(doc, events) {
  const out = []
  for (const e of events) {
    // Status and expiry are separate fields; the rest of the timeline is the event list.
    if (e.kind === 'legal-status' && (e.date === 'Status' || /expir/i.test(e.text))) continue
    const item = {}
    const date = ISO_DATE.test(e.date) ? e.date : ''
    if (date) item.date = date
    const text = short(e.text)
    if (text) item.text = text
    if (Object.keys(item).length) out.push(item)
  }
  for (const row of rowsOf(tableAfter(doc, 'legalEvents'))) {
    const c = cells(row)
    if (c.length < 3) continue
    const item = {}
    const date = short(c[0].textContent)
    if (date) item.date = date
    const code = clean(c[1].textContent)
    const title = clean(c[2].textContent)
    const desc = c[3] ? clean(c[3].textContent) : ''
    const text = short([code, title].filter(Boolean).join(' ') + (desc ? ` - ${desc}` : ''))
    if (text) item.text = text
    if (Object.keys(item).length) out.push(item)
  }
  return out.slice(0, CAPS.listItems)
}

function readFamily(doc) {
  const out = []
  const tl = first(doc, 'application-timeline')
  for (const block of all(tl, '.block')) {
    for (const sm of all(block, 'state-modifier')) {
      const tip = sm.nextElementSibling
      const lines = tip && tip.tagName === 'OVERLAY-TOOLTIP' ? all(tip, 'div').map((d) => clean(d.textContent)) : []
      const find = (label) => {
        const l = lines.find((x) => x.startsWith(label))
        return l ? clean(l.slice(label.length)) : ''
      }
      const result = sm.getAttribute('data-result') || ''
      const pub = result.split('/')[1] || ''
      const item = {}
      const number = short(pub || normalizePatentNumber(find('Application number:')))
      if (number) item.number = number
      const country = short(all(sm, 'span').find((x) => x.id === 'cc')?.textContent)
      if (country) item.country = country
      const status = short(find('Legal status:'))
      if (status) item.status = status
      if (Object.keys(item).length) out.push(item)
      if (out.length >= CAPS.listItems) return out
    }
  }
  return out
}

// ---- assembly -------------------------------------------------------------

/** Trim the least valuable parts until the serialized brief fits the backend cap. */
function fitTotal(brief) {
  const size = () => JSON.stringify(brief).length
  while (size() > CAPS.total && brief.description.length) brief.description.pop()
  while (size() > CAPS.total && brief.citations.cited_by.length) brief.citations.cited_by.length = Math.floor(brief.citations.cited_by.length / 2)
  while (size() > CAPS.total && brief.citations.patent.length) brief.citations.patent.length = Math.floor(brief.citations.patent.length / 2)
  while (size() > CAPS.total && brief.claims.length > 1) brief.claims.pop()
  return brief
}

/**
 * Turn a Google Patents page DOM into a Brief-shaped object.
 * @param {Document} doc
 * @param {string} url
 */
export function extractBrief(doc, url) {
  const number = safe(() => numberFromUrl(url), '') || safe(() => numberFromPage(doc), '')
  const events = safe(() => readTimeline(doc), [])
  const title = safe(() => readTitle(doc), '')
  const people = safe(() => readPeople(doc), { inventors: [], assignees: [] })
  const dates = safe(() => readDates(doc, events, number), {})
  const { status, expiry } = safe(() => readStatus(events), {})
  const abstract = safe(() => readAbstract(doc), '')
  const classifications = safe(() => readClassifications(doc), [])
  const landscapes = safe(() => readLandscapes(doc), [])
  const claims = safe(() => readClaims(doc), [])
  const description = safe(() => readDescription(doc), [])
  const citations = {
    patent: safe(() => readCitations(doc, 'patentCitations'), []),
    cited_by: safe(() => readCitations(doc, 'citedBy'), []),
  }
  const family = safe(() => readFamily(doc), [])
  const legalEvents = safe(() => readLegalEvents(doc, events), [])

  const brief = {
    patent_number: cut(number, 64),
    inventors: people.inventors.slice(0, CAPS.listItems).map((s) => cut(s, CAPS.short)),
    assignees: people.assignees.slice(0, CAPS.listItems).map((s) => cut(s, CAPS.short)),
    dates,
    classifications,
    landscapes,
    claims,
    description,
    citations,
    family,
    legal_events: legalEvents,
  }
  const t = short(title)
  if (t) brief.title = t
  if (abstract) brief.abstract = abstract
  const st = short(status)
  if (st) brief.status = st
  const ex = short(expiry)
  if (ex) brief.expiry = ex

  brief.completeness = {
    bibliographic: Boolean(t && (brief.inventors.length || brief.assignees.length || Object.keys(dates).length)),
    abstract: Boolean(abstract),
    claims: claims.length > 0,
    description: description.length > 0,
    classifications: classifications.length > 0,
    landscapes: landscapes.length > 0,
    citations: citations.patent.length > 0 || citations.cited_by.length > 0,
    family: family.length > 0,
    legal_events: legalEvents.length > 0,
  }
  for (const k of COMPLETENESS_KEYS) brief.completeness[k] = Boolean(brief.completeness[k])
  return fitTotal(brief)
}

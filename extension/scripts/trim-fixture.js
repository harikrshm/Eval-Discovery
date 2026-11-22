// Trim a raw "Webpage, Complete" save of a Google Patents page into a small
// fixture that keeps the real markup the extractor reads.
//
//   node scripts/trim-fixture.js <raw.html> <out.html>
//   node scripts/trim-fixture.js            (builds all four committed fixtures)
//
// Removes scripts, styles, svg, images, iframes, links, base, templates,
// tooltips and attribute noise; drops the sections the extractor ignores
// (similar documents, non-patent citations, images) and shortens the long
// tables and description (first rows / paragraphs kept; the "Patent Citations
// (558)" style headings keep the page's true counts). Offline: reads one local
// file, writes one local file.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { JSDOM } from 'jsdom'

const MAX_TABLE_ROWS = 30
const MAX_PARAGRAPHS_PER_SECTION = 6
const MAX_CLASSIFICATION_TREES = 40
const KEEP_META = /^(DC\.(title|date|contributor|description|type)|citation_patent_number|citation_patent_application_number|citation_patent_publication_number|description)$/

const REMOVE_SELECTORS = [
  'script', 'style', 'svg', 'img', 'iframe', 'link', 'base', 'noscript', 'template',
  'iron-icon', 'image-carousel', 'result-tags', 'bottom-bar',
  '.bottombar', '.knowledge-card-action-bar', 's-more-inline', '[id="margin"]',
  'section[id="thumbnails"]', 'figure-callout img',
]

function dropSection(doc, id) {
  const h3 = [...doc.querySelectorAll('h3')].find((h) => h.id === id)
  if (!h3) return
  let el = h3.nextElementSibling
  h3.remove()
  // table, its footnote div and any dom-if leftovers up to the next h3
  while (el && el.tagName !== 'H3') {
    const next = el.nextElementSibling
    if (el.classList.contains('responsive-table') || (el.tagName === 'DIV' && !el.id)) el.remove()
    el = next
  }
}

export function trim(rawHtml) {
  const src = new JSDOM(rawHtml).window.document
  const result = src.querySelector('patent-result')
  if (!result) throw new Error('no patent-result element: not a Google Patents page save')
  const out = new JSDOM('<!doctype html><html lang="en"><head><meta charset="utf-8"></head><body></body></html>').window.document

  const title = out.createElement('title')
  title.textContent = src.title
  out.head.appendChild(title)
  for (const m of src.querySelectorAll('meta[name]')) {
    if (!KEEP_META.test(m.getAttribute('name'))) continue
    const copy = out.createElement('meta')
    for (const a of ['name', 'content', 'scheme']) if (m.hasAttribute(a)) copy.setAttribute(a, m.getAttribute(a))
    out.head.appendChild(copy)
  }
  const clone = out.importNode(result, true)
  out.body.appendChild(clone)

  for (const sel of REMOVE_SELECTORS) for (const el of clone.querySelectorAll(sel)) el.remove()
  // Tooltips carry the family rows' application number and legal status; keep only those.
  for (const tip of clone.querySelectorAll('overlay-tooltip')) if (!tip.closest('application-timeline')) tip.remove()
  for (const id of ['nplCitations', 'similarDocuments']) dropSection(out, id)

  // Long tables: keep the first rows (family-citation header rows are kept as markers).
  for (const tbody of clone.querySelectorAll('.tbody')) {
    const rows = [...tbody.children].filter((r) => r.classList.contains('tr'))
    let kept = 0
    for (const r of rows) {
      if (r.querySelector('.famheader')) continue
      if (++kept > MAX_TABLE_ROWS) r.remove()
    }
  }

  // Classification viewer: one tree per code; keep the first ones.
  const trees = [...clone.querySelectorAll('classification-tree')]
  trees.slice(MAX_CLASSIFICATION_TREES).forEach((t) => (t.parentElement.tagName === 'DIV' && t.parentElement.children.length === 1 ? t.parentElement : t).remove())

  // Description: first paragraphs of each section, every heading kept.
  const desc = [...clone.querySelectorAll('patent-text')].find((p) => p.getAttribute('name') === 'description')
  if (desc) {
    let inSection = 0
    for (const el of desc.querySelectorAll('heading, .description-paragraph, .description-line')) {
      if (el.tagName === 'HEADING') inSection = 0
      else if (++inSection > MAX_PARAGRAPHS_PER_SECTION) (el.closest('li') || el).remove()
    }
  }

  // Attribute noise.
  for (const el of clone.querySelectorAll('*')) {
    for (const a of [...el.attributes]) {
      const n = a.name
      if (['style', 'act', 'href', 'target', 'tabindex', 'role', 'hidden', 'restamp'].includes(n) || n.startsWith('aria-') || n === 'mxw-id') {
        // hidden is meaningful only for the classification trees; the extractor ignores it.
        el.removeAttribute(n)
      }
    }
    const cls = (el.getAttribute('class') || '').split(/\s+/).filter((c) => c && c !== 'style-scope' && c !== 'x-scope' && !/^(patent-result|patent-text|classification-tree|classification-viewer|application-timeline|concept-mention|state-modifier|iron-icon-0)$/.test(c))
    if (cls.length) el.setAttribute('class', cls.join(' '))
    else el.removeAttribute('class')
  }
  // Comments and whitespace-only text.
  const walker = out.createTreeWalker(out.body, 128 | 4) // comments + text
  const dead = []
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.nodeType === 8) dead.push(n)
    else if (!n.textContent.trim()) n.textContent = ' '
  }
  dead.forEach((n) => n.remove())

  return '<!doctype html>\n' + out.documentElement.outerHTML.replace(/>\s{2,}</g, '> <') + '\n'
}

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..')
const BATCH = [
  ['EP4517554A1', 'ep4517554a1'],
  ['US12401720B1', 'us12401720b1'],
  ['US6756997B1', 'us6756997b1'],
  ['US20250119367A1', 'us20250119367a1'],
]

function main(argv) {
  if (argv.length >= 2) {
    fs.writeFileSync(argv[1], trim(fs.readFileSync(argv[0], 'utf8')))
    return
  }
  const rawDir = path.join(ROOT, 'fixtures', 'raw')
  const files = fs.readdirSync(rawDir).filter((f) => f.endsWith('.html'))
  for (const [prefix, name] of BATCH) {
    const f = files.find((x) => x.startsWith(prefix + ' '))
    if (!f) { console.error('missing raw page for', prefix); continue }
    const html = trim(fs.readFileSync(path.join(rawDir, f), 'utf8'))
    const dest = path.join(ROOT, 'fixtures', name + '.html')
    fs.writeFileSync(dest, html)
    console.log(name + '.html', (html.length / 1024).toFixed(0) + ' KB')
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main(process.argv.slice(2))

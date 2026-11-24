// Turns an assistant answer into safe DOM: a tiny markdown subset, validated reference pills and
// law ids. Everything is built with createElement and textContent; no HTML is ever parsed.
//
// Reference offsets are UTF-16 code-unit indexes into the answer string exactly as the backend
// returned it, so every slice here is taken on that raw string and only then run through
// displayText (which undoes the backend's &lt; &gt; &amp; escaping).

const ENTITIES = { '&lt;': '<', '&gt;': '>', '&amp;': '&' }
/** The backend escapes angle brackets in echoed page text; undo that for DISPLAY only (one pass, so &amp;lt; stays &lt;). */
export function displayText(answer) {
  return String(answer ?? '').replace(/&(?:lt|gt|amp);/g, (m) => ENTITIES[m])
}

const MAX_REFERENCES = 50
const MAX_FIELD = 100
const KINDS = new Set(['claim', 'paragraph', 'section'])
const LAW_ID = /\[(?:usc|mpep)-[^\]\s]+\]/gi

const BULLET = /^[ \t]*[-*•][ \t]+/
const NUMBERED = /^[ \t]*\d+[.)][ \t]+/
const HEADING = /^[ \t]*#+[ \t]*/

// ---- parsing ---------------------------------------------------------------

const isSpace = (ch) => ch === undefined || /\s/.test(ch)

/**
 * Inline runs for text[from, to). Paired **bold** and *italic* markers (the opener followed by,
 * the closer preceded by, a non-space) are dropped; backticks are dropped; any other marker stays
 * literal. Each run's text is exactly text.slice(run.start, run.start + run.text.length).
 */
function parseInline(text, from, to, bold, italic, out) {
  let bufStart = -1
  const flush = (end) => {
    if (bufStart >= 0 && end > bufStart) out.push({ text: text.slice(bufStart, end), start: bufStart, bold, italic })
    bufStart = -1
  }
  // A failed closer search from `open` also rules out every later opener of the same marker (it would
  // search a subset of the same text), so remember where each marker last failed: keeps this linear.
  const noCloserFrom = {}
  let i = from
  while (i < to) {
    const ch = text[i]
    if (ch === '`') {
      flush(i)
      i += 1
      continue
    }
    if (ch === '*') {
      const double = text[i + 1] === '*' && i + 1 < to
      const mark = double ? '**' : '*'
      const open = i + mark.length
      let close = -1
      if (open < to && !isSpace(text[open]) && !(open >= (noCloserFrom[mark] ?? Infinity))) {
        for (let j = text.indexOf(mark, open); j !== -1 && j + mark.length <= to; j = text.indexOf(mark, j + 1)) {
          // A single * must not be half of a **; the closer must follow a non-space.
          if (j > open && !isSpace(text[j - 1]) && (double || (text[j + 1] !== '*' && text[j - 1] !== '*'))) {
            close = j
            break
          }
        }
        if (close === -1) noCloserFrom[mark] = open
      }
      if (close !== -1) {
        flush(i)
        if (double) parseInline(text, open, close, true, italic, out)
        else parseInline(text, open, close, bold, true, out)
        i = close + mark.length
        continue
      }
      if (bufStart < 0) bufStart = i
      i += mark.length
      continue
    }
    if (bufStart < 0) bufStart = i
    i += 1
  }
  flush(to)
}

/** Blocks of `{ type: 'p' | 'ul' | 'ol', items: Inline[][] }`; Inline is `{ text, start, bold, italic }`. */
export function parseAnswer(text) {
  const src = String(text ?? '')
  const blocks = []
  let para = null // { from, to } of the open paragraph
  let list = null // the open ul/ol block

  const closePara = () => {
    if (para) {
      const runs = []
      parseInline(src, para.from, para.to, false, false, runs)
      blocks.push({ type: 'p', items: [runs] })
      para = null
    }
  }

  let pos = 0
  while (pos <= src.length) {
    let end = src.indexOf('\n', pos)
    if (end === -1) end = src.length
    const line = src.slice(pos, end)

    const listMatch = BULLET.exec(line) ?? NUMBERED.exec(line)
    const heading = !listMatch && HEADING.exec(line)
    if (/^\s*$/.test(line)) {
      closePara()
      list = null
    } else if (listMatch) {
      closePara()
      const type = BULLET.test(line) ? 'ul' : 'ol'
      if (!list || list.type !== type) {
        list = { type, items: [] }
        blocks.push(list)
      }
      const runs = []
      parseInline(src, pos + listMatch[0].length, end, false, false, runs)
      list.items.push(runs)
    } else if (heading) {
      closePara()
      list = null
      const runs = []
      parseInline(src, pos + heading[0].length, end, false, false, runs)
      blocks.push({ type: 'p', items: [runs] })
    } else {
      list = null
      if (para) para.to = end
      else para = { from: pos, to: end }
    }
    pos = end + 1
  }
  closePara()
  return blocks.filter((b) => b.items.some((item) => item.length > 0))
}

// ---- references --------------------------------------------------------------

const okString = (v) => typeof v === 'string' && v.length > 0 && v.length <= MAX_FIELD

/** Only well-formed references inside the answer, sorted, non-overlapping, at most 50. */
export function normalizeReferences(raw, answerLength) {
  if (!Array.isArray(raw)) return []
  const valid = []
  for (const r of raw) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) continue
    const { start, end, kind, target, label } = r
    if (!Number.isInteger(start) || !Number.isInteger(end)) continue
    if (start < 0 || start >= end || end > answerLength) continue
    if (!KINDS.has(kind) || !okString(target) || !okString(label)) continue
    valid.push({ start, end, kind, target, label })
  }
  valid.sort((a, b) => a.start - b.start)
  const out = []
  let lastEnd = 0
  for (const r of valid) {
    if (r.start < lastEnd) continue
    out.push(r)
    lastEnd = r.end
    if (out.length >= MAX_REFERENCES) break
  }
  return out
}

// ---- rendering -----------------------------------------------------------------

function appendWithLaws(doc, parent, raw) {
  let last = 0
  for (const m of raw.matchAll(LAW_ID)) {
    if (m.index > last) parent.appendChild(doc.createTextNode(displayText(raw.slice(last, m.index))))
    const span = doc.createElement('span')
    span.className = 'law'
    span.textContent = displayText(m[0])
    parent.appendChild(span)
    last = m.index + m[0].length
  }
  if (last < raw.length) parent.appendChild(doc.createTextNode(displayText(raw.slice(last))))
}

function appendRun(doc, parent, run, references, options) {
  let node = parent
  if (run.bold) node = node.appendChild(doc.createElement('strong'))
  if (run.italic) node = node.appendChild(doc.createElement('em'))
  const runEnd = run.start + run.text.length
  let cursor = run.start
  for (const ref of references) {
    // Only a reference wholly inside this run becomes a pill; one that crosses runs stays plain text.
    if (ref.start < cursor || ref.end > runEnd) continue
    if (ref.start > cursor) appendWithLaws(doc, node, run.text.slice(cursor - run.start, ref.start - run.start))
    if (options.pills) {
      const button = doc.createElement('button')
      button.setAttribute('type', 'button')
      button.className = 'pill'
      button.dataset.kind = ref.kind
      button.dataset.target = ref.target
      button.textContent = displayText(ref.label)
      button.addEventListener('click', () => options.onJump(ref))
      node.appendChild(button)
    } else {
      const span = doc.createElement('span')
      span.className = 'ref'
      span.textContent = displayText(ref.label)
      node.appendChild(span)
    }
    cursor = ref.end
  }
  if (cursor < runEnd) appendWithLaws(doc, node, run.text.slice(cursor - run.start))
}

/** The answer as a fragment of p/ul/ol/li/strong/em nodes, with reference pills (or plain refs) and law spans. */
export function renderAnswer(doc, answer, references, options) {
  const text = String(answer ?? '')
  const refs = normalizeReferences(references, text.length)
  const opts = { pills: Boolean(options?.pills), onJump: options?.onJump ?? (() => {}) }
  const fragment = doc.createDocumentFragment()
  for (const block of parseAnswer(text)) {
    const root = doc.createElement(block.type)
    for (const runs of block.items) {
      const holder = block.type === 'p' ? root : root.appendChild(doc.createElement('li'))
      for (const run of runs) appendRun(doc, holder, run, refs, opts)
    }
    fragment.appendChild(root)
  }
  return fragment
}

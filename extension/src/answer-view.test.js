import { describe, expect, it, vi } from 'vitest'
import { displayText, normalizeReferences, parseAnswer, renderAnswer } from './widget/answer-view.js'

const ref = (start, end, extra = {}) => ({ start, end, kind: 'claim', target: '1', label: 'claim 1', ...extra })
const render = (answer, refs = [], options = {}) => {
  const host = document.createElement('div')
  host.appendChild(renderAnswer(document, answer, refs, { pills: true, onJump: () => {}, ...options }))
  return host
}

describe('displayText', () => {
  it('un-escapes angle brackets and ampersands in one pass', () => {
    expect(displayText('a &lt;b&gt; &amp; c')).toBe('a <b> & c')
    expect(displayText('&amp;lt;')).toBe('&lt;')
    expect(displayText(undefined)).toBe('')
  })
})

describe('parseAnswer', () => {
  it('parses bold runs with their offsets', () => {
    const blocks = parseAnswer('**Hi** there')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('p')
    expect(blocks[0].items).toHaveLength(1)
    expect(blocks[0].items[0]).toEqual([
      { text: 'Hi', start: 2, bold: true, italic: false },
      { text: ' there', start: 6, bold: false, italic: false },
    ])
  })

  it('parses italics', () => {
    expect(parseAnswer('a *b* c')[0].items[0]).toEqual([
      { text: 'a ', start: 0, bold: false, italic: false },
      { text: 'b', start: 3, bold: false, italic: true },
      { text: ' c', start: 5, bold: false, italic: false },
    ])
  })

  it('splits paragraphs on blank lines', () => {
    const blocks = parseAnswer('one\n\ntwo')
    expect(blocks.map((b) => b.type)).toEqual(['p', 'p'])
    expect(blocks[1].items[0][0]).toEqual({ text: 'two', start: 5, bold: false, italic: false })
  })

  it('parses bullet lists', () => {
    const blocks = parseAnswer('- a\n- b')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('ul')
    expect(blocks[0].items).toHaveLength(2)
    expect(blocks[0].items[1][0]).toEqual({ text: 'b', start: 6, bold: false, italic: false })
    expect(parseAnswer('* a\n• b')[0].items).toHaveLength(2)
  })

  it('parses numbered lists', () => {
    const blocks = parseAnswer('1. a\n2) b')
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('ol')
    expect(blocks[0].items).toHaveLength(2)
  })

  it('keeps unpaired markers as literal text', () => {
    expect(parseAnswer('1 * 2 = 2')[0].items[0].map((r) => r.text).join('')).toBe('1 * 2 = 2')
    expect(parseAnswer('1 * 2 = 2')[0].items[0].every((r) => !r.italic && !r.bold)).toBe(true)
    expect(parseAnswer('a ** b')[0].items[0].map((r) => r.text).join('')).toBe('a ** b')
    expect(parseAnswer('a ** b')[0].items[0].every((r) => !r.bold)).toBe(true)
  })

  it('drops a leading # run and backticks', () => {
    expect(parseAnswer('# Title')[0].items[0].map((r) => r.text).join('')).toBe('Title')
    expect(parseAnswer('use `x` here')[0].items[0].map((r) => r.text).join('')).toBe('use x here')
  })

  it('still parses separate italics after the closer-search memoization', () => {
    expect(parseAnswer('*x* and *y*')[0].items[0]).toEqual([
      { text: 'x', start: 1, bold: false, italic: true },
      { text: ' and ', start: 3, bold: false, italic: false },
      { text: 'y', start: 9, bold: false, italic: true },
    ])
    expect(parseAnswer('*a *b* c')[0].items[0].filter((r) => r.italic).map((r) => r.text)).toEqual(['a *b'])
  })

  it.each(['*a ', '**a '])('parses a long run of unclosed %j markers in linear time', (unit) => {
    const src = unit.repeat(Math.ceil(20000 / unit.length))
    const t0 = performance.now()
    const blocks = parseAnswer(src)
    expect(performance.now() - t0).toBeLessThan(200)
    expect(blocks[0].items[0].map((r) => r.text).join('')).toBe(src)
  })

  it('keeps run text equal to the source at run.start', () => {
    const src = '## H\n\nA **b *c* d** `e` - f\n- x **y**\n2) z'
    for (const block of parseAnswer(src)) {
      for (const item of block.items) {
        for (const run of item) expect(src.slice(run.start, run.start + run.text.length)).toBe(run.text)
      }
    }
  })
})

describe('normalizeReferences', () => {
  it('keeps well-formed entries sorted by start', () => {
    const out = normalizeReferences([ref(10, 15), ref(0, 5)], 20)
    expect(out.map((r) => r.start)).toEqual([0, 10])
    expect(out[0]).toEqual({ start: 0, end: 5, kind: 'claim', target: '1', label: 'claim 1' })
  })

  it('drops overlapping entries (the earlier one wins)', () => {
    expect(normalizeReferences([ref(0, 5), ref(3, 8), ref(5, 9)], 20).map((r) => [r.start, r.end])).toEqual([
      [0, 5],
      [5, 9],
    ])
  })

  it('drops out-of-range and malformed entries', () => {
    const bad = [
      ref(-1, 3),
      ref(0, 21),
      ref(5, 5),
      ref(6, 5),
      ref(1.5, 4),
      ref('1', 4),
      ref(0, 3, { kind: 'case' }),
      ref(0, 3, { target: '' }),
      ref(0, 3, { label: 7 }),
      ref(0, 3, { target: 'x'.repeat(101) }),
      ref(0, 3, { label: 'x'.repeat(101) }),
      null,
      'x',
      [],
    ]
    expect(normalizeReferences(bad, 20)).toEqual([])
    expect(normalizeReferences({ start: 0 }, 20)).toEqual([])
    expect(normalizeReferences(undefined, 20)).toEqual([])
  })

  it('accepts 100-character strings and all three kinds', () => {
    const out = normalizeReferences(
      [ref(0, 1, { target: 'x'.repeat(100), label: 'y'.repeat(100) }), ref(1, 2, { kind: 'paragraph' }), ref(2, 3, { kind: 'section' })],
      5,
    )
    expect(out).toHaveLength(3)
  })

  it('keeps at most 50', () => {
    const many = Array.from({ length: 60 }, (_, i) => ref(i, i + 1))
    const out = normalizeReferences(many, 100)
    expect(out).toHaveLength(50)
    expect(out[49].start).toBe(49)
  })
})

describe('renderAnswer', () => {
  it('renders a pill after an emoji (UTF-16 offsets) and calls onJump once', () => {
    const answer = '😀 see claim 1.'
    const r = ref(7, 14)
    expect(answer.slice(7, 14)).toBe('claim 1')
    const onJump = vi.fn()
    const host = render(answer, [r], { pills: true, onJump })
    const button = host.querySelector('button.pill')
    expect(button.textContent).toBe('claim 1')
    expect(button.getAttribute('type')).toBe('button')
    expect(button.dataset.kind).toBe('claim')
    expect(button.dataset.target).toBe('1')
    expect(host.querySelector('p').textContent).toBe('😀 see claim 1.')
    button.click()
    expect(onJump).toHaveBeenCalledTimes(1)
    expect(onJump).toHaveBeenCalledWith(r)
  })

  it('renders a plain span.ref without pills', () => {
    const host = render('😀 see claim 1.', [ref(7, 14)], { pills: false })
    expect(host.querySelector('button')).toBeNull()
    expect(host.querySelector('span.ref').textContent).toBe('claim 1')
    expect(host.querySelector('p').textContent).toBe('😀 see claim 1.')
  })

  it('measures offsets on the raw string, before entities are undone', () => {
    const answer = 'a &lt; b see claim 2.'
    const start = answer.indexOf('claim 2')
    const host = render(answer, [ref(start, start + 7, { target: '2', label: 'claim 2' })])
    expect(host.querySelector('button.pill').textContent).toBe('claim 2')
    expect(host.querySelector('p').textContent).toBe('a < b see claim 2.')
  })

  it('renders a reference that crosses markdown markers as plain text', () => {
    const answer = 'see **claim** 1 now'
    const start = answer.indexOf('claim')
    const host = render(answer, [ref(start, answer.indexOf(' now'))])
    expect(host.querySelector('button')).toBeNull()
    expect(host.querySelector('.ref')).toBeNull()
    expect(host.querySelector('p').textContent).toBe('see claim 1 now')
    expect(host.querySelector('strong').textContent).toBe('claim')
  })

  it('renders a reference that spans blocks as plain text', () => {
    const answer = 'claim\n\n1 x'
    const host = render(answer, [ref(0, 8)])
    expect(host.querySelector('button')).toBeNull()
    expect(host.querySelectorAll('p')).toHaveLength(2)
  })

  it('shows markup in the answer as literal text, never as elements', () => {
    const host = render('hi <img onerror=x> and &lt;img src=x&gt;')
    expect(host.querySelector('img')).toBeNull()
    expect(host.querySelector('p').textContent).toBe('hi <img onerror=x> and <img src=x>')
  })

  it('renders law ids as span.law with their original text', () => {
    const host = render('See [usc-103] and [mpep-2106.04].')
    const laws = [...host.querySelectorAll('span.law')]
    expect(laws.map((n) => n.textContent)).toEqual(['[usc-103]', '[mpep-2106.04]'])
    expect(host.querySelector('p').textContent).toBe('See [usc-103] and [mpep-2106.04].')
  })

  it('builds lists, bold and italic', () => {
    const host = render('**Bold** and *it*\n\n- one\n- two\n\n1. a\n2. b')
    expect(host.querySelector('p > strong').textContent).toBe('Bold')
    expect(host.querySelector('p > em').textContent).toBe('it')
    expect([...host.querySelectorAll('ul > li')].map((n) => n.textContent)).toEqual(['one', 'two'])
    expect([...host.querySelectorAll('ol > li')].map((n) => n.textContent)).toEqual(['a', 'b'])
  })

  it('puts a pill inside a list item and bold run when it lies wholly in the run', () => {
    const answer = '- **claim 1** holds'
    const start = answer.indexOf('claim 1')
    const host = render(answer, [ref(start, start + 7)])
    expect(host.querySelector('li strong button.pill').textContent).toBe('claim 1')
  })

  it('renders empty or non-string input as an empty fragment', () => {
    expect(render('').childNodes).toHaveLength(0)
    expect(render(undefined).childNodes).toHaveLength(0)
  })
})

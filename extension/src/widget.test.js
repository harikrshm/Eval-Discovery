import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CHIPS } from './widget/chips.js'
import { FEEDBACK_AFTER_QUESTIONS } from './widget/followups.js'
import {
  INCOMPLETE_NOTE,
  LIMITS,
  NON_US_NOTICE,
  buildQuestion,
  createWidget,
  displayText,
  fitConversation,
  shouldReask,
} from './widget/Widget.js'

const BRIEF = {
  patent_number: 'US10000000B2',
  title: 'Thing',
  claims: [{ number: 1, text: 'A thing.' }],
  completeness: { claims: true, abstract: true },
}
const SESSION = { id: 'sess-1234-abcd', patentNumber: 'US10000000B2', brief: BRIEF }

let counter
function answerFor(overrides = {}) {
  counter += 1
  return {
    answer: `Answer ${counter}`,
    question_id: `q-${counter}`,
    intent: 'claim_explain',
    assist_version: 1,
    citation_check: { ok: true, unverified_claims: [], unverified_law: [] },
    ...overrides,
  }
}

function mount({ brief = BRIEF, chat, selection = '', ratingResult, jumper } = {}) {
  const api = {
    chat: vi.fn(chat ?? (async () => answerFor())),
    rating: vi.fn(ratingResult ?? (async () => ({}))),
    reask: vi.fn(async () => ({})),
  }
  const sel = { text: selection }
  const widget = createWidget({
    doc: document,
    session: { ...SESSION, brief },
    api,
    getSelection: () => sel.text,
    ...(jumper ? { jumper } : {}),
  })
  const shadow = widget.shadow
  const q = (s) => shadow.querySelector(s)
  const qa = (s) => [...shadow.querySelectorAll(s)]
  const flush = async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve()
  }
  const type = (text) => {
    const ta = q('textarea')
    ta.value = text
    ta.dispatchEvent(new Event('input', { bubbles: true }))
  }
  const send = async () => {
    q('button[data-action="send"]').click()
    await flush()
  }
  const ask = async (text) => {
    type(text)
    await send()
  }
  const chip = (label) => qa('button.chip').find((b) => b.textContent === label)
  const followup = () => qa('button.followup')
  return { widget, api, sel, q, qa, flush, type, send, ask, chip, followup }
}

let current
beforeEach(() => {
  vi.useFakeTimers()
  counter = 0
  document.body.innerHTML = ''
})
afterEach(() => {
  current?.widget.destroy()
  current = null
  vi.useRealTimers()
})
const m = (opts) => {
  current = mount(opts)
  return current
}

describe('helpers', () => {
  it('buildQuestion quotes the selection and appends the typed text, capped', () => {
    expect(buildQuestion('what does it mean', '')).toBe('what does it mean')
    expect(buildQuestion('', 'the passage')).toBe('Explain this passage: "the passage"')
    expect(buildQuestion('and why?', 'the passage')).toBe('Explain this passage: "the passage" and why?')
    expect(buildQuestion('  ', '  ')).toBe('')
    const long = buildQuestion('x'.repeat(5000), 'y'.repeat(5000))
    expect(long.length).toBeLessThanOrEqual(LIMITS.question)
    expect(long).toContain('y'.repeat(LIMITS.selection))
    expect(long).not.toContain('y'.repeat(LIMITS.selection + 1))
    expect(buildQuestion('', 'a\n  b\t c')).toBe('Explain this passage: "a b c"')
  })
  it('displayText un-escapes angle brackets and ampersands in one pass', () => {
    expect(displayText('a &lt;b&gt; &amp; c')).toBe('a <b> & c')
    expect(displayText('&amp;lt;')).toBe('&lt;')
    expect(displayText('plain')).toBe('plain')
  })
  it('shouldReask: same intent within 90 s only', () => {
    const prev = { intent: 'claim_explain', answeredAt: 1000 }
    expect(shouldReask(prev, 'claim_explain', 1000 + 90000)).toBe(true)
    expect(shouldReask(prev, 'claim_explain', 1000 + 90001)).toBe(false)
    expect(shouldReask(prev, 'other', 2000)).toBe(false)
    expect(shouldReask(undefined, 'other', 2000)).toBe(false)
  })
  it('fitConversation keeps at most 42 turns, starts with a user turn, caps by role', () => {
    const turns = []
    for (let i = 0; i < 30; i += 1) turns.push({ role: 'user', content: `u${i}` }, { role: 'assistant', content: `a${i}` })
    const out = fitConversation(turns)
    expect(out).toHaveLength(42)
    expect(out[0]).toEqual({ role: 'user', content: 'u9' })
    expect(out.at(-1)).toEqual({ role: 'assistant', content: 'a29' })
    const big = fitConversation([
      { role: 'user', content: 'u'.repeat(3000) },
      { role: 'assistant', content: 'a'.repeat(20000) },
    ])
    expect(big[0].content).toHaveLength(2000)
    expect(big[1].content).toHaveLength(16000)
  })
  it('fitConversation drops the oldest turns to stay under the request size', () => {
    const turns = []
    for (let i = 0; i < 10; i += 1) turns.push({ role: 'user', content: 'u' }, { role: 'assistant', content: 'a'.repeat(16000) })
    const out = fitConversation(turns)
    expect(out.reduce((n, t) => n + t.content.length, 0)).toBeLessThanOrEqual(LIMITS.requestChars)
    expect(out[0].role).toBe('user')
    expect(out.at(-1).role).toBe('assistant')
  })
  it('chips are the three fixed questions', () => {
    expect(CHIPS.map((c) => c.question)).toEqual([
      'Explain claim 1',
      'What do these CPC codes mean?',
      'Is this still in force?',
    ])
  })
})

describe('widget DOM', () => {
  it('lives in a shadow root and adds no style or other element to the page', () => {
    const stylesBefore = document.querySelectorAll('style, link').length
    const { widget, q } = m()
    expect(document.body.children).toHaveLength(1)
    expect(document.body.firstElementChild).toBe(widget.host)
    expect(widget.host.shadowRoot).toBeNull() // closed: the page cannot reach into it
    expect(widget.shadow).toBeInstanceOf(ShadowRoot)
    expect(widget.host.children).toHaveLength(0)
    expect(document.querySelectorAll('style, link').length).toBe(stylesBefore)
    expect(document.head.querySelector('style')).toBeNull()
    expect(widget.shadow.querySelector('style')).not.toBeNull()
    expect(document.querySelector('textarea')).toBeNull()
    expect(q('.launcher')).not.toBeNull()
  })
  it('is closed by default; the launcher opens it and Escape closes it', () => {
    const { q } = m()
    const panel = q('.panel')
    const launcher = q('.launcher')
    expect(panel.hidden).toBe(true)
    expect(launcher.getAttribute('aria-expanded')).toBe('false')
    expect(launcher.getAttribute('aria-label')).toBeTruthy()
    launcher.click()
    expect(panel.hidden).toBe(false)
    expect(launcher.getAttribute('aria-expanded')).toBe('true')
    q('textarea').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, composed: true }))
    expect(panel.hidden).toBe(true)
    expect(launcher.getAttribute('aria-expanded')).toBe('false')
  })
  it('is accessible: log region, labelled controls, footer', () => {
    const { q, qa } = m()
    const log = q('[role="log"]')
    expect(log.getAttribute('aria-live')).toBe('polite')
    expect(q('textarea').getAttribute('aria-label')).toBeTruthy()
    for (const b of qa('button')) {
      expect((b.textContent.trim() || b.getAttribute('aria-label') || '').length).toBeGreaterThan(0)
    }
    expect(q('.footer').textContent).toMatch(/Not legal advice/)
  })
  it('shows the patent number as a chip in the header, set as text', () => {
    const { q } = m()
    const chip = q('.panel > header .pn')
    expect(chip).not.toBeNull()
    expect(chip.tagName).toBe('SPAN')
    expect(chip.textContent).toBe(BRIEF.patent_number)
    expect(chip.children).toHaveLength(0)
    expect(q('.panel > header h2').textContent).toBe('Patent Assist')
    expect(q('.close').getAttribute('aria-label')).toBe('Close Patent Assist')
    expect(q('.close').textContent).toBe('×')
  })
  it('keeps the patent number text-only even when it holds markup', () => {
    const { q } = m({ brief: { ...BRIEF, patent_number: '<img src=x onerror=alert(1)>' } })
    expect(q('.panel > header .pn').textContent).toBe('<img src=x onerror=alert(1)>')
    expect(q('.panel > header img')).toBeNull()
  })
  describe('styles', () => {
    const css = () => m().q('style').textContent
    const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '')
    it('define every --pa- token they use, in the :host block, and use no raw colours elsewhere', () => {
      const text = stripComments(css())
      const hostBlock = text.match(/:host\s*\{([^}]*)\}/)[1]
      const defined = new Set([...hostBlock.matchAll(/(--pa-[a-z0-9-]+)\s*:/g)].map((x) => x[1]))
      const used = new Set([...text.matchAll(/var\(\s*(--pa-[a-z0-9-]+)/g)].map((x) => x[1]))
      expect(used.size).toBeGreaterThan(10)
      for (const token of used) expect(defined, token).toContain(token)
      const outside = text.replace(/:host\s*\{[^}]*\}/, '')
      expect(outside).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
      expect(outside).not.toMatch(/\b(?:rgb|rgba|hsl|hsla)\(/)
    })
    it('do not use pre-wrap, keep [hidden] and a reduced-motion rule', () => {
      const text = css()
      expect(text).not.toContain('pre-wrap')
      expect(text).toMatch(/\[hidden\]\s*\{\s*display:\s*none\s*!important/)
      expect(text).toContain('prefers-reduced-motion: reduce')
      expect(text).toContain('overflow-wrap: anywhere')
    })
    it('style the new answer, pill, follow-up and feedback classes', () => {
      const text = css()
      for (const sel of ['.pill', '.pill:disabled', '.law', '.ref', '.followup', '.feedback', '.msg .text p', '.msg .text ul', '.msg .text ol', '.pn']) {
        expect(text, sel).toContain(sel)
      }
    })
    it('give buttons a 32 px minimum height', () => {
      const text = stripComments(css())
      expect(text).toMatch(/--pa-target:\s*32px/)
      expect(text).toMatch(/(?:^|\n)button\s*\{[^}]*min-height:\s*var\(--pa-target\)/)
    })
  })
  it('Enter sends, Shift+Enter does not', async () => {
    const { api, q, type, flush } = m()
    q('.launcher').click()
    type('Hello there')
    const ta = q('textarea')
    ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true }))
    await flush()
    expect(api.chat).not.toHaveBeenCalled()
    const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    ta.dispatchEvent(ev)
    await flush()
    expect(ev.defaultPrevented).toBe(true)
    expect(api.chat).toHaveBeenCalledTimes(1)
  })
  describe('keystrokes stay inside the widget', () => {
    const KEYS = ['i', 'w', 's', 'a', 'd', 'ArrowLeft']
    const TYPES = ['keydown', 'keyup', 'keypress']
    const fire = (target, type, key) =>
      target.dispatchEvent(new KeyboardEvent(type, { key, bubbles: true, composed: true, cancelable: true }))
    const watch = () => {
      const onDoc = vi.fn()
      const onWin = vi.fn()
      for (const t of TYPES) {
        document.addEventListener(t, onDoc)
        window.addEventListener(t, onWin)
      }
      return {
        onDoc,
        onWin,
        stop: () => {
          for (const t of TYPES) {
            document.removeEventListener(t, onDoc)
            window.removeEventListener(t, onWin)
          }
        },
      }
    }

    it('does not let page-level listeners see keys typed in the textarea', () => {
      const { q } = m()
      q('.launcher').click()
      const w = watch()
      try {
        for (const type of TYPES) for (const key of KEYS) fire(q('textarea'), type, key)
        expect(w.onDoc).not.toHaveBeenCalled()
        expect(w.onWin).not.toHaveBeenCalled()
      } finally {
        w.stop()
      }
    })
    it('control: the same keys on document.body still reach those listeners', () => {
      m()
      const w = watch()
      try {
        for (const type of TYPES) for (const key of KEYS) fire(document.body, type, key)
        expect(w.onDoc).toHaveBeenCalledTimes(TYPES.length * KEYS.length)
        expect(w.onWin).toHaveBeenCalledTimes(TYPES.length * KEYS.length)
      } finally {
        w.stop()
      }
    })
    it('Enter in the textarea still sends and is not leaked to the page', async () => {
      const { api, q, type, flush } = m()
      q('.launcher').click()
      type('Hello there')
      const w = watch()
      try {
        fire(q('textarea'), 'keydown', 'Enter')
        await flush()
        expect(api.chat).toHaveBeenCalledTimes(1)
        expect(w.onDoc).not.toHaveBeenCalled()
      } finally {
        w.stop()
      }
    })
    it('Escape still closes the panel and focuses the launcher', () => {
      const { widget, q } = m()
      q('.launcher').click()
      const w = watch()
      try {
        fire(q('textarea'), 'keydown', 'Escape')
        expect(q('.panel').hidden).toBe(true)
        expect(widget.shadow.activeElement).toBe(q('.launcher'))
        expect(w.onDoc).not.toHaveBeenCalled()
      } finally {
        w.stop()
      }
    })
  })
  it('caps the input at 2000 characters', () => {
    const { q } = m()
    expect(q('textarea').maxLength).toBe(2000)
  })
  it('destroy removes the host and listeners', () => {
    const { widget, sel } = m()
    widget.destroy()
    expect(document.body.children).toHaveLength(0)
    sel.text = 'x'
    document.dispatchEvent(new Event('selectionchange'))
    widget.destroy() // idempotent
  })
  it('replaces a stale widget host left by an earlier run', () => {
    m()
    const second = createWidget({ doc: document, session: SESSION, api: { chat: vi.fn() }, getSelection: () => '' })
    expect(document.querySelectorAll('[data-patent-assist]')).toHaveLength(1)
    second.destroy()
  })
})

describe('non-US and completeness', () => {
  it('a non-US patent shows the notice and sends nothing', async () => {
    const { api, q, qa, flush } = m({ brief: { ...BRIEF, patent_number: 'EP4517554A1' } })
    q('.launcher').click()
    expect(q('.notice').hidden).toBe(false)
    expect(q('.notice').textContent).toBe(NON_US_NOTICE)
    expect(q('textarea').disabled).toBe(true)
    expect(qa('button.chip').every((b) => b.disabled)).toBe(true)
    q('textarea').value = 'hi'
    q('button[data-action="send"]').click()
    qa('button.chip')[0].click()
    await flush()
    expect(api.chat).not.toHaveBeenCalled()
    expect(api.rating).not.toHaveBeenCalled()
  })
  it('a US patent hides the notice', () => {
    const { q } = m()
    expect(q('.notice').hidden).toBe(true)
  })
  it('shows a small note when sections are missing', () => {
    const { q } = m({ brief: { ...BRIEF, completeness: { claims: true, description: false } } })
    expect(q('.note').hidden).toBe(false)
    expect(q('.note').textContent).toBe(INCOMPLETE_NOTE)
    current.widget.destroy()
    const second = mount({ brief: { ...BRIEF, completeness: { claims: true } } })
    expect(second.q('.note').hidden).toBe(true)
    second.widget.destroy()
  })
})

describe('chat', () => {
  it('sends the session id, brief, history and question; the history is this session only', async () => {
    const { api, ask, q } = m()
    q('.launcher').click()
    await ask('What is claim 1 about?')
    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(api.chat.mock.calls[0][0]).toEqual({
      session_id: 'sess-1234-abcd',
      brief: BRIEF,
      history: [],
      question: 'What is claim 1 about?',
    })
    await ask('And claim 2?')
    expect(api.chat.mock.calls[1][0]).toEqual({
      session_id: 'sess-1234-abcd',
      brief: BRIEF,
      history: [
        { role: 'user', content: 'What is claim 1 about?' },
        { role: 'assistant', content: 'Answer 1' },
      ],
      question: 'And claim 2?',
    })
  })
  it('a new widget (new patent page) starts with an empty history', async () => {
    const first = m()
    await first.ask('one')
    first.widget.destroy()
    const second = mount()
    current = second
    await second.ask('two')
    expect(second.api.chat.mock.calls[0][0].history).toEqual([])
  })
  it('keeps at most 20 history turns and sends the full assistant answer back', async () => {
    const big = 'z'.repeat(15000)
    const { api, ask } = m({ chat: async () => answerFor({ answer: big }) })
    for (let i = 0; i < 12; i += 1) await ask(`q${i}`)
    const last = api.chat.mock.calls.at(-1)[0]
    expect(last.history.length).toBeLessThanOrEqual(LIMITS.historyTurns)
    expect(last.history.length % 2).toBe(0)
    expect(last.history.at(-1).content).toBe(big)
    const chars = last.question.length + last.history.reduce((n, t) => n + t.content.length, 0)
    expect(chars).toBeLessThanOrEqual(LIMITS.requestChars)
    expect(last.history[0].role).toBe('user')
  })
  it('the selection becomes the quoted question', async () => {
    const { api, ask, sel, type, send } = m()
    sel.text = '  A processor\n configured   to run  '
    await ask('what is this?')
    expect(api.chat.mock.calls[0][0].question).toBe('Explain this passage: "A processor configured to run" what is this?')
    type('')
    await send()
    expect(api.chat).toHaveBeenCalledTimes(2)
    expect(api.chat.mock.calls[1][0].question).toBe('Explain this passage: "A processor configured to run"')
  })
  it('caps the selection at 800 characters', async () => {
    const { api, ask, sel } = m()
    sel.text = 'w'.repeat(3000)
    await ask('')
    const question = api.chat.mock.calls[0][0].question
    expect(question).toBe(`Explain this passage: "${'w'.repeat(800)}"`)
  })
  it('remembers a selection made before the click moved it, shows it, and can clear it', async () => {
    const { api, ask, sel, q } = m()
    sel.text = 'remembered text'
    document.dispatchEvent(new Event('selectionchange'))
    sel.text = '' // clicking the input cleared the page selection
    expect(q('.selection').hidden).toBe(false)
    expect(q('.selection').textContent).toContain('remembered text')
    await ask('why?')
    expect(api.chat.mock.calls[0][0].question).toBe('Explain this passage: "remembered text" why?')
    expect(q('.selection').hidden).toBe(true) // used once
    await ask('next')
    expect(api.chat.mock.calls[1][0].question).toBe('next')
    sel.text = 'again'
    document.dispatchEvent(new Event('selectionchange'))
    q('.selection button').click()
    sel.text = ''
    await ask('later')
    expect(api.chat.mock.calls[2][0].question).toBe('later')
  })
  it('does not send an empty question', async () => {
    const { api, ask } = m()
    await ask('   ')
    expect(api.chat).not.toHaveBeenCalled()
  })
  it('a chip sends its fixed text as an ordinary question (not the selection)', async () => {
    const { api, chip, flush, sel, type, q } = m()
    sel.text = 'some passage'
    type('half typed draft')
    chip(CHIPS[1].label).click()
    await flush()
    expect(api.chat.mock.calls.map((c) => c[0].question)).toEqual(['What do these CPC codes mean?'])
    expect(q('textarea').value).toBe('half typed draft')
  })
  it('disables the input while waiting and shows "Thinking…"', async () => {
    let resolve
    const { api, ask, q, flush } = m({ chat: () => new Promise((r) => (resolve = r)) })
    await ask('hello')
    expect(q('textarea').disabled).toBe(true)
    expect(q('button[data-action="send"]').disabled).toBe(true)
    expect(q('.thinking').textContent).toBe('Thinking…')
    // a second send while waiting is ignored
    q('button[data-action="send"]').click()
    expect(api.chat).toHaveBeenCalledTimes(1)
    resolve(answerFor())
    await flush()
    expect(q('textarea').disabled).toBe(false)
    expect(q('.thinking')).toBeNull()
  })
  it('clears the input after sending', async () => {
    const { ask, q } = m()
    await ask('hello')
    expect(q('textarea').value).toBe('')
  })
})

describe('answer rendering', () => {
  it('renders with textContent: markup in an answer creates no element', async () => {
    const evil = 'Look <img src=x onerror=alert(1)> and <script>alert(2)</script> end'
    const { ask, q, qa } = m({ chat: async () => answerFor({ answer: evil }) })
    await ask('hi')
    const log = q('[role="log"]')
    expect(log.querySelector('img')).toBeNull()
    expect(log.querySelector('script')).toBeNull()
    expect(qa('.msg.assistant')[0].querySelector('.text').textContent).toBe(evil)
  })
  it('un-escapes entities for display only; the history keeps the original text', async () => {
    const escaped = 'Claim says &lt;img src=x onerror=alert(1)&gt; &amp; more'
    const { api, ask, q } = m({ chat: async () => answerFor({ answer: escaped }) })
    await ask('first')
    const text = q('.msg.assistant .text')
    expect(text.textContent).toBe('Claim says <img src=x onerror=alert(1)> & more')
    expect(q('[role="log"]').querySelector('img')).toBeNull()
    await ask('second')
    expect(api.chat.mock.calls[1][0].history[1]).toEqual({ role: 'assistant', content: escaped })
  })
  it('renders the user question as text too', async () => {
    const { ask, q } = m()
    await ask('<b>bold</b>')
    expect(q('[role="log"]').querySelector('b')).toBeNull()
    expect(q('.msg.user .text').textContent).toBe('<b>bold</b>')
  })
})

describe('errors', () => {
  it('shows the fixed text and a Retry button; retry resends the same question without duplicating it', async () => {
    let fail = true
    const { api, ask, q, qa, flush } = m({
      chat: async () => (fail ? { ok: false, status: 502, message: 'Something provider-specific and secret' } : answerFor()),
    })
    await ask('hello')
    expect(q('.error').textContent).toContain('The assistant could not answer right now. Please try again.')
    expect(q('.error').textContent).not.toContain('secret')
    expect(q('textarea').disabled).toBe(false)
    expect(q('.thinking')).toBeNull()
    fail = false
    q('.error button[data-action="retry"]').click()
    await flush()
    expect(api.chat).toHaveBeenCalledTimes(2)
    expect(api.chat.mock.calls[1][0].question).toBe('hello')
    expect(api.chat.mock.calls[1][0].history).toEqual([])
    expect(q('.error')).toBeNull()
    expect(qa('.msg.user')).toHaveLength(1)
    expect(qa('.msg.assistant')).toHaveLength(1)
  })
  it('a failed question is not part of the history', async () => {
    let fail = true
    const { api, ask } = m({ chat: async () => (fail ? { ok: false, status: 0, message: 'x' } : answerFor()) })
    await ask('lost')
    fail = false
    await ask('kept')
    expect(api.chat.mock.calls[1][0].history).toEqual([])
  })
  it('shows a fixed rate-limit text for 429 and survives a rejected call or malformed answer', async () => {
    const replies = [
      { ok: false, status: 429, message: 'Too many requests. Please wait a moment and try again.' },
      Promise.reject(new Error('boom')),
      { answer: 42 },
    ]
    const { ask, q } = m({ chat: async () => replies.shift() })
    await ask('a')
    expect(q('.error').textContent).toContain('Too many requests')
    await ask('b')
    expect(q('.error').textContent).toContain('The assistant could not answer right now')
    expect(q('.error')).not.toBeNull()
    await ask('c')
    expect(q('.error').textContent).toContain('The assistant could not answer right now')
    expect(q('textarea').disabled).toBe(false)
  })
})

describe('chips and follow-ups', () => {
  it('shows the chip row before the first question and hides it from then on', async () => {
    const { ask, q, qa } = m()
    expect(q('.chips').hidden).toBe(false)
    expect(qa('button.chip')).toHaveLength(3)
    await ask('first')
    expect(q('.chips').hidden).toBe(true)
    await ask('second')
    expect(q('.chips').hidden).toBe(true)
  })
  it('hides the chips as soon as the first question is sent, and a retry does not bring them back', async () => {
    let fail = true
    const { ask, q, flush } = m({ chat: async () => (fail ? { ok: false, status: 502, message: 'x' } : answerFor()) })
    await ask('hello')
    expect(q('.chips').hidden).toBe(true)
    fail = false
    q('.error button[data-action="retry"]').click()
    await flush()
    expect(q('.chips').hidden).toBe(true)
  })
  it('clicking a chip hides the row', async () => {
    const { chip, flush, q } = m()
    chip('Explain claim 1').click()
    await flush()
    expect(q('.chips').hidden).toBe(true)
  })
  it('after an answer there is one follow-up button; clicking it sends exactly that text', async () => {
    const { api, ask, followup, flush } = m()
    expect(followup()).toHaveLength(0)
    await ask('what is claim 1')
    expect(followup()).toHaveLength(1)
    expect(followup()[0].dataset.action).toBe('followup')
    const text = followup()[0].textContent
    expect(text).toBe('What do these CPC codes mean?') // claim_explain, no claim cited: the claim candidate is skipped
    followup()[0].click()
    await flush()
    expect(api.chat).toHaveBeenCalledTimes(2)
    expect(api.chat.mock.calls[1][0].question).toBe(text)
  })
  it('the follow-up is replaced, never duplicated, and never repeats a question already asked', async () => {
    const { ask, followup, q } = m()
    await ask('one')
    expect(followup().map((b) => b.textContent)).toEqual(['What do these CPC codes mean?'])
    await ask('two')
    expect(followup().map((b) => b.textContent)).toEqual(['What do these CPC codes mean?'])
    expect(q('.followups').children).toHaveLength(1)
    await ask('  what do these CPC codes MEAN?  ')
    expect(followup().map((b) => b.textContent)).toEqual(['Is this still in force?'])
    await ask('Is this still in force?')
    expect(followup()).toHaveLength(0) // nothing left to suggest
  })
  it('suggests the next claim after the highest one cited', async () => {
    const brief = { ...BRIEF, claims: [1, 2, 3].map((number) => ({ number, text: 'x' })) }
    const references = [{ start: 0, end: 7, kind: 'claim', target: '1', label: 'Claim 1' }]
    const { ask, followup } = m({ brief, chat: async () => answerFor({ answer: 'Claim 1 is wide.', references }) })
    await ask('explain it')
    expect(followup()[0].textContent).toBe('What does claim 2 add?')
  })
  it('is disabled while a question is pending', async () => {
    let resolve
    const queue = [async () => answerFor(), () => new Promise((r) => (resolve = r))]
    const { ask, followup, flush } = m({ chat: () => queue.shift()() })
    await ask('one')
    await ask('two')
    expect(followup()[0].disabled).toBe(true)
    resolve(answerFor())
    await flush()
    expect(followup()[0].disabled).toBe(false)
  })
  it('a failed question leaves a follow-up that does not duplicate the failed text', async () => {
    const queue = [() => answerFor(), () => ({ ok: false, status: 502, message: 'x' })]
    const { ask, followup, q } = m({ chat: async () => queue.shift()() })
    await ask('one')
    followup()[0].click()
    await current.flush()
    expect(q('.error')).not.toBeNull()
    expect(followup().map((b) => b.textContent)).toEqual(['Is this still in force?'])
  })
  it('a non-US page offers neither a follow-up nor an enabled chip', () => {
    const { followup, qa } = m({ brief: { ...BRIEF, patent_number: 'EP1234567B1' } })
    expect(followup()).toHaveLength(0)
    expect(qa('button.chip').every((b) => b.disabled)).toBe(true)
  })
})

describe('feedback request', () => {
  const buttons = (q) => ['up', 'down', 'no_answer', 'dismiss'].map((a) => q(`button[data-action="${a}"]`))
  it('answers 1 and 2 carry no rating buttons', async () => {
    const { ask, q, qa } = m()
    await ask('one')
    await ask('two')
    expect(q('.feedback')).toBeNull()
    expect(qa('button[data-action="up"], button[data-action="down"], button[data-action="no_answer"]')).toHaveLength(0)
  })
  it('appears once, under answer 3, with the three rating buttons, a reason form and Dismiss', async () => {
    expect(FEEDBACK_AFTER_QUESTIONS).toBe(3)
    const { ask, q, qa } = m()
    await ask('one')
    await ask('two')
    await ask('three')
    expect(qa('.feedback')).toHaveLength(1)
    const answers = qa('.msg.assistant')
    expect(answers[2].querySelector('.feedback')).not.toBeNull()
    expect(q('.feedback').textContent).toContain('How is this going?')
    const [up, down, none, dismiss] = buttons(q)
    expect(up.textContent).toBe('Helpful')
    expect(up.getAttribute('aria-label')).toBe('Thumbs up: this answer helped')
    expect(down.textContent).toBe('Not helpful')
    expect(down.getAttribute('aria-label')).toBe('Thumbs down: this answer did not help')
    expect(none.textContent).toBe("Didn't answer my question")
    expect(dismiss.textContent).toBe('Dismiss')
    expect(q('.feedback .reason').hidden).toBe(true)
  })
  it('does not come back for answers 4 and 5 once it was shown', async () => {
    const { ask, qa } = m()
    for (const t of ['1', '2', '3', '4', '5']) await ask(t)
    expect(qa('.feedback')).toHaveLength(1)
    expect(qa('.msg.assistant')[2].querySelector('.feedback')).not.toBeNull()
  })
  it('Dismiss removes it for good', async () => {
    const { ask, q, qa, api, flush } = m()
    for (const t of ['1', '2', '3']) await ask(t)
    q('button[data-action="dismiss"]').click()
    await flush()
    expect(q('.feedback')).toBeNull()
    for (const t of ['4', '5', '6']) await ask(t)
    expect(qa('.feedback')).toHaveLength(0)
    expect(api.rating).not.toHaveBeenCalled()
  })
  it('rating it locks it and it does not return', async () => {
    const { ask, q, qa, flush } = m()
    for (const t of ['1', '2', '3']) await ask(t)
    q('button[data-action="up"]').click()
    await flush()
    expect(q('.feedback button[data-action="up"]').disabled).toBe(true)
    expect(q('.feedback .thanks').textContent).toBe('Thanks for the feedback.')
    for (const t of ['4', '5']) await ask(t)
    expect(qa('.feedback')).toHaveLength(1)
  })
  it('failed questions do not count toward the third answer', async () => {
    const queue = [() => answerFor(), () => ({ ok: false, status: 500, message: 'x' }), () => answerFor(), () => answerFor()]
    const { ask, q } = m({ chat: async () => queue.shift()() })
    await ask('1')
    await ask('2') // fails
    await ask('3')
    expect(q('.feedback')).toBeNull()
    await ask('4')
    expect(q('.feedback')).not.toBeNull()
  })
})

describe('ratings', () => {
  const rateable = async (opts) => {
    const ctx = m(opts)
    await ctx.ask('q one')
    await ctx.ask('q two')
    await ctx.ask('q three')
    return ctx
  }
  it('thumbs up sends a rating with the conversation so far and the brief', async () => {
    const { api, q, flush } = await rateable()
    q('button[data-action="up"]').click()
    await flush()
    expect(api.rating).toHaveBeenCalledTimes(1)
    expect(api.rating.mock.calls[0][0]).toEqual({
      question_id: 'q-3',
      rating: 'up',
      conversation: [
        { role: 'user', content: 'q one' },
        { role: 'assistant', content: 'Answer 1' },
        { role: 'user', content: 'q two' },
        { role: 'assistant', content: 'Answer 2' },
        { role: 'user', content: 'q three' },
        { role: 'assistant', content: 'Answer 3' },
      ],
      brief: BRIEF,
    })
  })
  it('a rating given after answer 3 is about answer 3 and the conversation so far', async () => {
    const { api, q, flush } = await rateable()
    q('button[data-action="up"]').click()
    await flush()
    expect(api.rating).toHaveBeenCalledTimes(1)
    expect(api.rating.mock.calls[0][0].question_id).toBe('q-3')
    expect(api.rating.mock.calls[0][0].conversation).toHaveLength(6)
  })
  it('a rating given after the user kept asking is about the newest answer and the whole conversation', async () => {
    const { api, ask, q, qa, flush } = await rateable()
    await ask('q four')
    await ask('q five')
    expect(qa('.feedback')).toHaveLength(1) // still the one card, under answer 3
    q('button[data-action="up"]').click()
    await flush()
    expect(api.rating).toHaveBeenCalledTimes(1)
    const body = api.rating.mock.calls[0][0]
    expect(body.question_id).toBe('q-5')
    expect(body.conversation).toHaveLength(10)
    expect(body.conversation.slice(-2)).toEqual([
      { role: 'user', content: 'q five' },
      { role: 'assistant', content: 'Answer 5' },
    ])
    expect(q('.feedback button[data-action="up"]').disabled).toBe(true) // the card's own lock
  })
  it('Not helpful asks for an optional reason (max 500) and sends one rating with it', async () => {
    const { api, q, flush } = await rateable()
    q('button[data-action="down"]').click()
    await flush()
    expect(api.rating).not.toHaveBeenCalled()
    const reason = q('input[data-field="reason"]')
    expect(q('.feedback .reason').hidden).toBe(false)
    expect(reason.maxLength).toBe(500)
    expect(reason.getAttribute('aria-label')).toBeTruthy()
    reason.value = 'wrong claim'
    q('button[data-action="send-reason"]').click()
    await flush()
    expect(api.rating).toHaveBeenCalledTimes(1)
    const body = api.rating.mock.calls[0][0]
    expect(body).toMatchObject({ question_id: 'q-3', rating: 'down', reason: 'wrong claim' })
    expect(body.conversation).toHaveLength(6)
    expect(body.conversation.at(-1)).toEqual({ role: 'assistant', content: 'Answer 3' })
  })
  it('Not helpful without a reason omits the field', async () => {
    const { api, q, flush } = await rateable()
    q('button[data-action="down"]').click()
    q('button[data-action="send-reason"]').click()
    await flush()
    expect(api.rating.mock.calls[0][0]).not.toHaveProperty('reason')
    expect(api.rating.mock.calls[0][0].rating).toBe('down')
  })
  it('"Didn\'t answer my question" sends the no_answer rating', async () => {
    const { api, q, flush } = await rateable()
    q('button[data-action="no_answer"]').click()
    await flush()
    expect(api.rating.mock.calls[0][0]).toMatchObject({ question_id: 'q-3', rating: 'no_answer' })
  })
  it('rates once', async () => {
    const { api, q, flush } = await rateable()
    q('button[data-action="up"]').click()
    q('button[data-action="up"]').click()
    q('button[data-action="no_answer"]').click()
    await flush()
    expect(api.rating).toHaveBeenCalledTimes(1)
    expect(q('button[data-action="up"]').disabled).toBe(true)
  })
  it('a failing rating never blocks the UI; one silent retry, none for 429', async () => {
    const { api, ask, q, flush } = await rateable({ ratingResult: async () => ({ ok: false, status: 502, message: 'x' }) })
    q('button[data-action="up"]').click()
    await flush()
    expect(q('.error')).toBeNull()
    expect(q('textarea').disabled).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.rating).toHaveBeenCalledTimes(2) // original + one retry
    await vi.advanceTimersByTimeAsync(60000)
    expect(api.rating).toHaveBeenCalledTimes(2)
    await ask('still works')
    expect(api.chat).toHaveBeenCalledTimes(4)
  })
  it('a rejected rating call is retried once; 429 is dropped silently', async () => {
    const m1 = await rateable({ ratingResult: async () => Promise.reject(new Error('gone')) })
    m1.q('button[data-action="up"]').click()
    await vi.advanceTimersByTimeAsync(10000)
    expect(m1.api.rating).toHaveBeenCalledTimes(2)
    m1.widget.destroy()
    counter = 0
    const m2 = mount({ ratingResult: async () => ({ ok: false, status: 429, message: 'x' }) })
    current = m2
    for (const t of ['a', 'b', 'c']) await m2.ask(t)
    m2.q('button[data-action="up"]').click()
    await vi.advanceTimersByTimeAsync(10000)
    expect(m2.api.rating).toHaveBeenCalledTimes(1)
  })
  it('destroying the widget cancels a pending retry', async () => {
    const { api, q, widget } = await rateable({ ratingResult: async () => ({ ok: false, status: 500, message: 'x' }) })
    q('button[data-action="up"]').click()
    await vi.advanceTimersByTimeAsync(0)
    widget.destroy()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.rating).toHaveBeenCalledTimes(1)
  })
})

describe('reference pills', () => {
  const paragraphAnswer = (text = 'See paragraph [0042] for details.') => ({
    answer: text,
    references: [{ start: text.indexOf('[0042]'), end: text.indexOf('[0042]') + 6, kind: 'paragraph', target: '0042', label: '[0042]' }],
  })
  const fakeJumper = (canJump = () => true) => ({ canJump: vi.fn(canJump), jump: vi.fn(() => true), clear: vi.fn() })
  const withParagraph = () => {
    const p = document.createElement('div')
    p.className = 'description-paragraph'
    p.setAttribute('num', '0042')
    document.body.append(p)
    return p
  }

  it('on a granted patent a paragraph reference renders a pill that calls the jumper with that reference', async () => {
    withParagraph()
    const jumper = fakeJumper()
    const { ask, q, qa } = m({ jumper, chat: async () => answerFor(paragraphAnswer()) })
    await ask('where?')
    const pills = qa('button.pill')
    expect(pills).toHaveLength(1)
    expect(pills[0].textContent).toBe('[0042]')
    expect(pills[0].disabled).toBe(false)
    pills[0].click()
    expect(jumper.jump).toHaveBeenCalledTimes(1)
    expect(jumper.jump).toHaveBeenCalledWith(expect.objectContaining({ kind: 'paragraph', target: '0042', label: '[0042]' }))
    expect(q('.msg.assistant .text').textContent).toBe('See paragraph [0042] for details.')
  })
  it('works end to end with the real jumper against the page DOM', async () => {
    const p = withParagraph()
    p.scrollIntoView = vi.fn()
    const { ask, qa } = m({ chat: async () => answerFor(paragraphAnswer()) })
    await ask('where?')
    qa('button.pill')[0].click()
    expect(p.scrollIntoView).toHaveBeenCalledTimes(1)
    expect(p.style.outline).toContain('solid')
  })
  it('the same answer on a patent application (A1) has no pill', async () => {
    withParagraph()
    const { ask, qa, q } = m({
      brief: { ...BRIEF, patent_number: 'US20200012345A1' },
      chat: async () => answerFor(paragraphAnswer()),
    })
    await ask('where?')
    expect(qa('button.pill')).toHaveLength(0)
    expect(q('.msg.assistant .ref').textContent).toBe('[0042]')
  })
  it('disables a pill whose target is not on the page, with a tooltip', async () => {
    const jumper = fakeJumper(() => false)
    const { ask, qa } = m({ jumper, chat: async () => answerFor(paragraphAnswer()) })
    await ask('where?')
    const pill = qa('button.pill')[0]
    expect(jumper.canJump).toHaveBeenCalledWith({ kind: 'paragraph', target: '0042' })
    expect(pill.disabled).toBe(true)
    expect(pill.title).toBe("Couldn't find this on the page")
  })
  it('keeps the raw answer as history even when it has formatting and references', async () => {
    const raw = '**Bold** see [0042] &lt;x&gt;'
    const start = raw.indexOf('[0042]')
    const refs = [{ start, end: start + 6, kind: 'paragraph', target: '0042', label: '[0042]' }]
    const { api, ask, flush, followup } = m({ chat: async () => answerFor({ answer: raw, references: refs }) })
    await ask('one')
    followup()[0].click()
    await flush()
    expect(api.chat.mock.calls[1][0].history[1]).toEqual({ role: 'assistant', content: raw })
  })
  it('ignores malformed references', async () => {
    const { ask, qa } = m({ chat: async () => answerFor({ answer: 'Short.', references: [{ start: 0, end: 99, kind: 'claim', target: '1', label: 'x' }, 'junk'] }) })
    await ask('hi')
    expect(qa('button.pill')).toHaveLength(0)
  })
  it('destroy clears the jumper highlight', () => {
    const jumper = fakeJumper()
    const { widget } = m({ jumper })
    widget.destroy()
    expect(jumper.clear).toHaveBeenCalledTimes(1)
  })
})

describe('re-ask rule', () => {
  const replies = (list) => {
    const queue = [...list]
    return async () => answerFor(queue.shift() ?? {})
  }
  it('fires for the previous question when the next same-intent answer arrives within 90 s', async () => {
    const { api, ask, flush } = m({ chat: replies([{ intent: 'claim_explain' }, { intent: 'claim_explain' }]) })
    await ask('explain claim 1')
    vi.advanceTimersByTime(60000)
    await ask('what does claim 1 mean')
    await flush()
    expect(api.reask).toHaveBeenCalledTimes(1)
    const body = api.reask.mock.calls[0][0]
    expect(body.question_id).toBe('q-1')
    expect(body.brief).toEqual(BRIEF)
    expect(body.conversation).toEqual([
      { role: 'user', content: 'explain claim 1' },
      { role: 'assistant', content: 'Answer 1' },
      { role: 'user', content: 'what does claim 1 mean' },
      { role: 'assistant', content: 'Answer 2' },
    ])
  })
  it('does not fire for a different intent', async () => {
    const { api, ask } = m({ chat: replies([{ intent: 'claim_explain' }, { intent: 'status_family' }]) })
    await ask('a')
    await ask('b')
    expect(api.reask).not.toHaveBeenCalled()
  })
  it('does not fire after 90 seconds', async () => {
    const { api, ask } = m({ chat: replies([{ intent: 'claim_explain' }, { intent: 'claim_explain' }]) })
    await ask('a')
    vi.advanceTimersByTime(91000)
    await ask('b')
    expect(api.reask).not.toHaveBeenCalled()
  })
  it('only compares with the immediately previous question, and only once per question', async () => {
    const { api, ask } = m({
      chat: replies([{ intent: 'claim_explain' }, { intent: 'other' }, { intent: 'claim_explain' }, { intent: 'claim_explain' }]),
    })
    await ask('a')
    await ask('b')
    await ask('c') // previous (b) is "other": no
    expect(api.reask).not.toHaveBeenCalled()
    await ask('d') // c and d same intent
    expect(api.reask).toHaveBeenCalledTimes(1)
    expect(api.reask.mock.calls[0][0].question_id).toBe('q-3')
  })
  it('a failing re-ask never blocks the UI (one silent retry)', async () => {
    const { api, ask, q } = m({ chat: replies([{ intent: 'x' }, { intent: 'x' }]) })
    api.reask.mockImplementation(async () => ({ ok: false, status: 500, message: 'x' }))
    await ask('a')
    await ask('b')
    expect(q('.error')).toBeNull()
    await vi.advanceTimersByTimeAsync(10000)
    expect(api.reask).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(60000)
    expect(api.reask).toHaveBeenCalledTimes(2)
  })
  it('a failed question in between does not count as an answer', async () => {
    const queue = [{ intent: 'x' }, { ok: false, status: 500, message: 'x' }, { intent: 'x' }]
    const { api, ask } = m({ chat: async () => {
      const n = queue.shift()
      return n.ok === false ? n : answerFor(n)
    } })
    await ask('a')
    await ask('b') // fails
    await ask('c')
    expect(api.reask).toHaveBeenCalledTimes(1)
    expect(api.reask.mock.calls[0][0].conversation.map((t) => t.content)).toEqual(['a', 'Answer 1', 'c', 'Answer 2'])
  })
})

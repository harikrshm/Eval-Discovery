import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SECTION_NAMES, SECTION_SELECTORS, createTracker } from './signals.js'
import { startContent } from './wiring.js'

const KEYS = ['bounce', 'dwell_ms', 'patent_number', 'sections', 'session_id']
const SESSION = { id: 'sess-1234-abcd', patentNumber: 'US10000000B2' }

/** A stand-in for IntersectionObserver whose entries are driven by the test. */
class FakeIO {
  static instances = []
  constructor(callback, options = {}) {
    this.callback = callback
    this.options = options
    this.targets = new Set()
    this.disconnected = false
    FakeIO.instances.push(this)
  }
  observe(el) {
    this.targets.add(el)
  }
  unobserve(el) {
    this.targets.delete(el)
  }
  disconnect() {
    this.targets.clear()
    this.disconnected = true
  }
}
const isBand = (io) => io.options.rootMargin !== undefined
function setRatio(el, ratio) {
  for (const io of FakeIO.instances) {
    if (!isBand(io) && io.targets.has(el)) io.callback([{ target: el, isIntersecting: ratio > 0, intersectionRatio: ratio }])
  }
}
function setBand(el, inBand, height) {
  for (const io of FakeIO.instances) {
    if (isBand(io) && io.targets.has(el)) {
      io.callback([{ target: el, isIntersecting: inBand, intersectionRatio: 0, boundingClientRect: { height } }])
    }
  }
}

let t // the injected clock (ms)
const now = () => t
/** Move the clock and the fake timers forward together, one second at a time so timers see the right time. */
async function advance(ms, stepMs = 1000) {
  let left = ms
  while (left > 0) {
    const step = Math.min(stepMs, left)
    t += step
    await vi.advanceTimersByTimeAsync(step)
    left -= step
  }
}
const flushMicrotasks = async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve()
}

// One element per section found through the REAL selectors from SECTION_SELECTORS.
function buildPage() {
  document.body.innerHTML = `
    <patent-text name="claims"></patent-text>
    <patent-text name="description"></patent-text>
    <classification-viewer></classification-viewer>
    <div class="footer">
      <h3 id="patentCitations"></h3><div class="responsive-table" id="t-cit"></div>
      <h3 id="citedBy"></h3><div class="responsive-table" id="t-citedby"></div>
      <h3 id="legalEvents"></h3><div class="responsive-table" id="t-legal"></div>
    </div>
    <application-timeline></application-timeline>
    <landscape-link></landscape-link><landscape-link></landscape-link>`
  return {
    claims: document.querySelector('patent-text[name="claims"]'),
    description: document.querySelector('patent-text[name="description"]'),
    classifications: document.querySelector('classification-viewer'),
    citations: document.getElementById('t-cit'),
    citedBy: document.getElementById('t-citedby'),
    legal: document.getElementById('t-legal'),
    family: document.querySelector('application-timeline'),
    landscape: document.querySelector('landscape-link'),
  }
}

function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

let trackers
let send
function make(overrides = {}) {
  const tracker = createTracker(document, {
    session: SESSION,
    now,
    sendHeartbeat: send,
    IntersectionObserverImpl: FakeIO,
    ...overrides,
  })
  trackers.push(tracker)
  return tracker
}

beforeEach(() => {
  vi.useFakeTimers()
  t = 0
  FakeIO.instances = []
  trackers = []
  send = vi.fn(async () => ({ ok: true }))
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  document.body.innerHTML = ''
})
afterEach(() => {
  while (trackers.length) trackers.pop().destroy()
  delete document.visibilityState
  vi.useRealTimers()
})

describe('SECTION_SELECTORS', () => {
  it('has exactly the seven section names', () => {
    expect(Object.keys(SECTION_SELECTORS).sort()).toEqual([...SECTION_NAMES].sort())
    expect([...SECTION_NAMES]).toEqual(['claims', 'description', 'classifications', 'citations', 'family', 'legal_events', 'landscapes'])
  })
  it('observes the real page elements', () => {
    const els = buildPage()
    make()
    const observed = FakeIO.instances.find((io) => !isBand(io)).targets
    for (const el of [els.claims, els.description, els.classifications, els.citations, els.citedBy, els.legal, els.family, els.landscape]) {
      expect(observed.has(el)).toBe(true)
    }
    expect(observed.size).toBe(9) // two landscape links
  })
})

describe('time accounting', () => {
  it('counts a section only while at least 40% is visible and the tab is visible', async () => {
    const els = buildPage()
    const tracker = make()
    await advance(2000)
    expect(tracker.snapshot().sections.claims).toBe(0)

    setRatio(els.claims, 0.3) // below the threshold
    await advance(2000)
    expect(tracker.snapshot().sections.claims).toBe(0)

    setRatio(els.claims, 0.4)
    await advance(3000)
    expect(tracker.snapshot().sections.claims).toBe(3000)

    setVisibility('hidden')
    await advance(5000) // hidden: nothing
    expect(tracker.snapshot().sections.claims).toBe(3000)
    setVisibility('visible')
    await advance(1000)
    expect(tracker.snapshot().sections.claims).toBe(4000)

    setRatio(els.claims, 0.1)
    await advance(2000)
    expect(tracker.snapshot().sections.claims).toBe(4000)
    expect(tracker.snapshot().dwell_ms).toBe(10000)
  })

  it('the hidden tab does not accumulate dwell, also when it starts hidden', async () => {
    setVisibility('hidden')
    const tracker = make()
    await advance(20000)
    expect(tracker.snapshot().dwell_ms).toBe(0)
    expect(send).not.toHaveBeenCalled()
    setVisibility('visible')
    await advance(2000)
    expect(tracker.snapshot().dwell_ms).toBe(2000)
  })

  it('a section is visible while any one of its elements is', async () => {
    const els = buildPage()
    const tracker = make()
    setRatio(els.citations, 0.9)
    setRatio(els.citedBy, 0.9)
    await advance(1000)
    setRatio(els.citations, 0)
    await advance(1000)
    setRatio(els.citedBy, 0)
    await advance(1000)
    expect(tracker.snapshot().sections.citations).toBe(2000) // counted once, not per element
  })

  it('a section taller than the viewport counts while it overlaps the middle band', async () => {
    const els = buildPage()
    vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(800)
    const tracker = make()
    setRatio(els.description, 0.05) // far below 40% of its own height
    await advance(1000)
    expect(tracker.snapshot().sections.description).toBe(0)
    setBand(els.description, true, 9000)
    await advance(2000)
    expect(tracker.snapshot().sections.description).toBe(2000)
    setBand(els.description, false, 9000)
    await advance(1000)
    expect(tracker.snapshot().sections.description).toBe(2000)
    // a short element in the band does not get the fallback
    setBand(els.claims, true, 500)
    await advance(1000)
    expect(tracker.snapshot().sections.claims).toBe(0)
  })

  it('never accumulates more than the backend allows and tolerates a clock that goes backwards', async () => {
    const tracker = make()
    t = 100000
    await advance(1000)
    const before = tracker.snapshot().dwell_ms
    t = 50000 // backwards
    await advance(0)
    expect(tracker.snapshot().dwell_ms).toBeGreaterThanOrEqual(before)
    expect(tracker.snapshot().dwell_ms).toBeLessThanOrEqual(86_400_000)
  })

  it('works without IntersectionObserver (dwell only)', async () => {
    buildPage()
    const tracker = createTracker(document, { session: SESSION, now, sendHeartbeat: send, IntersectionObserverImpl: null })
    trackers.push(tracker)
    await advance(2000)
    expect(tracker.snapshot().dwell_ms).toBe(2000)
    expect(Object.values(tracker.snapshot().sections).every((v) => v === 0)).toBe(true)
  })
})

describe('bounce', () => {
  it.each([
    ['short and nothing viewed', 5000, 0, true],
    ['short, a section viewed for 3 s', 5000, 3000, false],
    ['short, a section visible only 2.9 s', 5000, 2900, true],
    ['long dwell without a viewed section', 12000, 0, false],
    ['dwell of exactly 10 s', 10000, 0, false],
    ['dwell just under 10 s', 9000, 2000, true],
  ])('%s', async (_label, dwell, sectionMs, bounce) => {
    const els = buildPage()
    const tracker = make()
    if (sectionMs) {
      setRatio(els.claims, 1)
      await advance(sectionMs)
      setRatio(els.claims, 0)
    }
    await advance(dwell - sectionMs)
    const snap = tracker.snapshot()
    expect(snap.dwell_ms).toBe(dwell)
    expect(snap.bounce).toBe(bounce)
  })
})

describe('payload', () => {
  it('has exactly the five keys and the seven section names, all non-negative integers', async () => {
    const els = buildPage()
    make()
    setRatio(els.claims, 0.8)
    t = 1000.4
    await vi.advanceTimersByTimeAsync(0)
    t = 7777.777
    await advance(0)
    await advance(20000, 2500.3)
    expect(send).toHaveBeenCalled()
    for (const [payload] of send.mock.calls) {
      expect(Object.keys(payload).sort()).toEqual(KEYS)
      expect(Object.keys(payload.sections).sort()).toEqual([...SECTION_NAMES].sort())
      expect(payload.session_id).toBe(SESSION.id)
      expect(payload.patent_number).toBe('US10000000B2')
      expect(typeof payload.bounce).toBe('boolean')
      for (const v of [payload.dwell_ms, ...Object.values(payload.sections)]) {
        expect(Number.isInteger(v)).toBe(true)
        expect(v).toBeGreaterThanOrEqual(0)
        expect(v).toBeLessThanOrEqual(86_400_000)
      }
    }
  })

  it('normalises the patent number to the compact form', () => {
    make({ session: { id: SESSION.id, patentNumber: 'US 10,000,000 B2' } })
    const [tracker] = trackers
    expect(tracker.snapshot().patent_number).toBe('US10000000B2')
  })
})

describe('heartbeats', () => {
  it('sends nothing before 15 s, the first heartbeat at 15 s, then every 15 s with cumulative totals', async () => {
    make()
    await advance(14000)
    expect(send).not.toHaveBeenCalled()
    await advance(1000)
    expect(send).toHaveBeenCalledTimes(1)
    expect(send.mock.calls[0][0].dwell_ms).toBe(15000)
    await advance(15000)
    expect(send).toHaveBeenCalledTimes(2)
    await advance(15000)
    expect(send).toHaveBeenCalledTimes(3)
    const dwells = send.mock.calls.map(([p]) => p.dwell_ms)
    expect(dwells).toEqual([15000, 30000, 45000])
  })

  it('totals never decrease across heartbeats', async () => {
    const els = buildPage()
    make()
    const dwells = []
    const secs = []
    send.mockImplementation(async (p) => {
      dwells.push(p.dwell_ms)
      secs.push(p.sections.claims)
      return { ok: true }
    })
    setRatio(els.claims, 0.9)
    await advance(20000)
    setRatio(els.claims, 0)
    setVisibility('hidden')
    await advance(20000)
    setVisibility('visible')
    await advance(40000)
    expect(dwells.length).toBeGreaterThanOrEqual(3)
    for (let i = 1; i < dwells.length; i += 1) {
      expect(dwells[i]).toBeGreaterThanOrEqual(dwells[i - 1])
      expect(secs[i]).toBeGreaterThanOrEqual(secs[i - 1])
    }
  })

  it('does not re-send an identical payload (a hidden tab changes nothing)', async () => {
    make()
    await advance(15000)
    expect(send).toHaveBeenCalledTimes(1)
    setVisibility('hidden') // flush: nothing changed since the 15 s heartbeat
    expect(send).toHaveBeenCalledTimes(1)
    await advance(60000)
    expect(send).toHaveBeenCalledTimes(1)
  })

  it('sends nothing when nothing was ever visible (all zero)', async () => {
    setVisibility('hidden')
    make()
    await advance(60000)
    window.dispatchEvent(new Event('pagehide'))
    expect(send).not.toHaveBeenCalled()
  })

  describe('flush', () => {
    it('pagehide sends once and not twice', async () => {
      make()
      await advance(5000)
      window.dispatchEvent(new Event('pagehide'))
      expect(send).toHaveBeenCalledTimes(1)
      expect(send.mock.calls[0][0].dwell_ms).toBe(5000)
      window.dispatchEvent(new Event('pagehide'))
      await flushMicrotasks()
      expect(send).toHaveBeenCalledTimes(1)
    })

    it('visibilitychange to hidden sends once; to visible sends nothing', async () => {
      make()
      await advance(5000)
      setVisibility('hidden')
      expect(send).toHaveBeenCalledTimes(1)
      expect(send.mock.calls[0][0].dwell_ms).toBe(5000)
      setVisibility('visible')
      expect(send).toHaveBeenCalledTimes(1)
    })

    it('destroy sends the final totals once; a flush right before makes it a no-op', async () => {
      const a = make()
      await advance(5000)
      a.destroy()
      expect(send).toHaveBeenCalledTimes(1)
      expect(send.mock.calls[0][0].dwell_ms).toBe(5000)
      a.destroy()
      expect(send).toHaveBeenCalledTimes(1)

      send.mockClear()
      const b = make()
      await advance(5000)
      window.dispatchEvent(new Event('pagehide'))
      await flushMicrotasks()
      b.destroy()
      expect(send).toHaveBeenCalledTimes(1)
    })

    it('a session that lived less than a second sends nothing', async () => {
      const a = make()
      await advance(500)
      window.dispatchEvent(new Event('pagehide'))
      a.destroy()
      expect(send).not.toHaveBeenCalled()
    })
  })

  describe('delivery', () => {
    it('keeps at most one send in flight and sends the latest totals once after it', async () => {
      let release
      send.mockImplementationOnce(() => new Promise((r) => (release = r)))
      make()
      await advance(15000) // first heartbeat, still in flight
      expect(send).toHaveBeenCalledTimes(1)
      await advance(3000)
      window.dispatchEvent(new Event('pagehide')) // wants to send, one is in flight
      window.dispatchEvent(new Event('pagehide'))
      expect(send).toHaveBeenCalledTimes(1)
      release({ ok: true })
      await flushMicrotasks()
      expect(send).toHaveBeenCalledTimes(2)
      expect(send.mock.calls[1][0].dwell_ms).toBe(18000)
      await flushMicrotasks()
      expect(send).toHaveBeenCalledTimes(2)
    })

    it('a destroy while a send is in flight still delivers its final totals, once, and nothing later', async () => {
      let release
      send.mockImplementationOnce(() => new Promise((r) => (release = r)))
      const tracker = make()
      await advance(15000)
      await advance(2000)
      tracker.destroy()
      expect(send).toHaveBeenCalledTimes(1)
      release({ ok: true })
      await flushMicrotasks()
      expect(send).toHaveBeenCalledTimes(2)
      expect(send.mock.calls[1][0].dwell_ms).toBe(17000)
      await advance(60000)
      window.dispatchEvent(new Event('pagehide'))
      expect(send).toHaveBeenCalledTimes(2)
    })

    it.each([
      ['a rejected promise', () => Promise.reject(new Error('gone'))],
      ['a synchronous throw', () => { throw new Error('boom') }],
      ['an HTTP 429 answer', async () => ({ ok: false, status: 429, message: 'x' })],
      ['a failure answer', async () => ({ ok: false, status: 0, message: 'x' })],
    ])('%s is silent, is not retried in a loop and the next interval carries the totals again', async (_label, impl) => {
      make()
      send.mockImplementation(impl)
      await advance(15000)
      expect(send).toHaveBeenCalledTimes(1)
      await flushMicrotasks()
      await vi.advanceTimersByTimeAsync(0)
      expect(send).toHaveBeenCalledTimes(1) // no immediate retry
      send.mockImplementation(async () => ({ ok: true }))
      await advance(15000)
      expect(send).toHaveBeenCalledTimes(2)
      expect(send.mock.calls[1][0].dwell_ms).toBe(30000)
    })
  })
})

describe('destroy', () => {
  it('removes timers, observers and listeners; nothing more is sent', async () => {
    const els = buildPage()
    const addTarget = vi.spyOn(document, 'removeEventListener')
    const addWindow = vi.spyOn(window, 'removeEventListener')
    const tracker = make()
    await advance(3000)
    tracker.destroy()
    send.mockClear()
    expect(vi.getTimerCount()).toBe(0)
    expect(FakeIO.instances.every((io) => io.disconnected)).toBe(true)
    expect(addTarget.mock.calls.map((c) => c[0])).toContain('visibilitychange')
    expect(addWindow.mock.calls.map((c) => c[0])).toContain('pagehide')

    setRatio(els.claims, 1)
    setVisibility('hidden')
    setVisibility('visible')
    window.dispatchEvent(new Event('pagehide'))
    await advance(60000)
    expect(send).not.toHaveBeenCalled()
  })

  it('a following session gets a new tracker that starts from zero', async () => {
    const els = buildPage()
    const first = make()
    setRatio(els.claims, 1)
    await advance(20000)
    first.destroy()
    send.mockClear()
    const second = make({ session: { id: 'sess-9999-zzzz', patentNumber: 'US10000001B2' } })
    expect(second.snapshot()).toMatchObject({ session_id: 'sess-9999-zzzz', patent_number: 'US10000001B2', dwell_ms: 0 })
    expect(second.snapshot().sections.claims).toBe(0)
    await advance(15000)
    expect(send).toHaveBeenCalledTimes(1)
    expect(send.mock.calls[0][0]).toMatchObject({ session_id: 'sess-9999-zzzz', dwell_ms: 15000 })
    expect(send.mock.calls[0][0].sections.claims).toBe(0)
  })
})

describe('US patents only', () => {
  it.each(['EP4517554A1', 'WO2020123456A1', 'JP2020123456A', ''])('%s: no tracker, no timers, no observers, nothing sent', async (number) => {
    buildPage()
    const tracker = make({ session: { id: SESSION.id, patentNumber: number } })
    await advance(60000)
    window.dispatchEvent(new Event('pagehide'))
    tracker.destroy()
    expect(send).not.toHaveBeenCalled()
    expect(FakeIO.instances).toHaveLength(0)
    expect(vi.getTimerCount()).toBe(0)
  })
})

describe('late-rendered sections', () => {
  it('picks up an element that appears after the start, through the debounced mutation observer', async () => {
    const tracker = make()
    expect(FakeIO.instances[0].targets.size).toBe(0)
    const claims = document.createElement('patent-text')
    claims.setAttribute('name', 'claims')
    document.body.append(claims)
    await advance(100)
    expect(FakeIO.instances[0].targets.has(claims)).toBe(false) // debounced
    await advance(1000)
    expect(FakeIO.instances[0].targets.has(claims)).toBe(true)
    setRatio(claims, 0.7)
    await advance(2000)
    expect(tracker.snapshot().sections.claims).toBe(2000)
    // a section that never renders stays at zero
    expect(tracker.snapshot().sections.family).toBe(0)
  })

  it('stops observing an element that left the page', async () => {
    const els = buildPage()
    make()
    els.claims.remove()
    await advance(1500)
    expect(FakeIO.instances[0].targets.has(els.claims)).toBe(false)
  })
})

describe('privacy: what the tracker may not touch', () => {
  const source = readFileSync(resolve(import.meta.dirname, 'signals.js'), 'utf8')
  it.each([
    'scroll',
    'copy',
    'cut',
    'paste',
    'keydown',
    'keyup',
    'keypress',
    'click',
    'mouse',
    'localStorage',
    'sessionStorage',
    'cookie',
    'chrome\\.storage',
    'indexedDB',
    'location\\.href',
    'location\\.search',
    'document\\.location',
    'getSelection',
    'innerText',
    'textContent',
    'innerHTML',
    'outerHTML',
    'XMLHttpRequest',
    'fetch\\(',
    'sendBeacon',
    'chrome\\.',
    'href',
  ])('the source never uses %s', (word) => {
    expect(source).not.toMatch(new RegExp(word, 'i'))
  })

  it('registers listeners for visibilitychange and pagehide only', () => {
    const docSpy = vi.spyOn(document, 'addEventListener')
    const winSpy = vi.spyOn(window, 'addEventListener')
    buildPage()
    make()
    const types = [...docSpy.mock.calls, ...winSpy.mock.calls].map((c) => c[0])
    expect(new Set(types)).toEqual(new Set(['visibilitychange', 'pagehide']))
  })
})

// ---- through the session controller (wiring) ------------------------------------------------

describe('wired to the session controller', () => {
  const clientRender = (number = 'US1000000B2') => {
    document.body.innerHTML = `<patent-result><section class="knowledge-card"><header>
      <h2 id="pubnum">${number}</h2><h1 id="title">A title</h1></header></section>
      <patent-text name="claims"><section class="flex"><div class="claims"><div class="claim" num="00001">1. A thing.</div></div></section></patent-text></patent-result>`
  }
  const apiMock = () => ({
    chat: vi.fn(async () => ({ answer: 'ok', question_id: 'q1', intent: 'other' })),
    rating: vi.fn(async () => ({})),
    reask: vi.fn(async () => ({})),
    pageView: vi.fn(async () => ({ ok: true })),
  })
  let content
  const start = (api, extract) => {
    content = startContent({
      win: window,
      api,
      extract: extract ?? ((_doc, url) => ({
        patent_number: new URL(url).pathname.split('/')[2],
        title: 'A title',
        claims: [{ number: 1, text: 'A thing.' }],
        completeness: { claims: true },
      })),
      trackerOptions: { IntersectionObserverImpl: FakeIO },
    })
    return content
  }
  const tick = (ms = 500) => vi.advanceTimersByTimeAsync(ms)

  beforeEach(() => {
    window.history.replaceState(null, '', '/patent/US1000000B2/en')
    document.body.innerHTML = ''
  })
  afterEach(() => {
    content?.destroy()
    content = null
  })

  it('starts on a patent route once the page is ready and sends cumulative heartbeats for the session', async () => {
    clientRender()
    const api = apiMock()
    start(api)
    await tick()
    expect(api.pageView).not.toHaveBeenCalled()
    await tick(15000)
    expect(api.pageView).toHaveBeenCalledTimes(1)
    const payload = api.pageView.mock.calls[0][0]
    expect(Object.keys(payload).sort()).toEqual(KEYS)
    expect(payload.patent_number).toBe('US1000000B2')
    expect(payload.dwell_ms).toBeGreaterThanOrEqual(14000)
    expect(payload.dwell_ms).toBeLessThanOrEqual(15500)
  })

  it('a search route starts no tracker and sends nothing', async () => {
    window.history.replaceState(null, '', '/?q=thermostat')
    clientRender()
    const api = apiMock()
    start(api)
    await tick(60000)
    window.dispatchEvent(new Event('pagehide'))
    expect(api.pageView).not.toHaveBeenCalled()
    expect(FakeIO.instances).toHaveLength(0)
  })

  it('a non-US patent route starts no tracker (the widget shows its notice only)', async () => {
    window.history.replaceState(null, '', '/patent/EP4517554A1/en')
    clientRender('EP4517554A1')
    const api = apiMock()
    start(api)
    await tick(60000)
    expect(api.pageView).not.toHaveBeenCalled()
    expect(FakeIO.instances).toHaveLength(0)
  })

  it('leaving the patent flushes once, stops, and the next patent gets a new tracker from zero', async () => {
    clientRender()
    const api = apiMock()
    start(api)
    await tick()
    await tick(5000)
    window.history.pushState(null, '', '/?q=thermostat')
    await tick(3000)
    expect(api.pageView).toHaveBeenCalledTimes(1) // the final flush
    const first = api.pageView.mock.calls[0][0]
    await tick(60000)
    expect(api.pageView).toHaveBeenCalledTimes(1) // nothing after the route change

    window.history.pushState(null, '', '/patent/US2000000B2/en')
    clientRender('US2000000B2')
    await tick()
    await tick(15000)
    expect(api.pageView).toHaveBeenCalledTimes(2)
    const second = api.pageView.mock.calls[1][0]
    expect(second.session_id).not.toBe(first.session_id)
    expect(second.patent_number).toBe('US2000000B2')
    expect(second.dwell_ms).toBeLessThanOrEqual(15500) // not carried over from the first page view
  })

  it('a rejected page view and a missing api.pageView never reach the page', async () => {
    clientRender()
    const api = apiMock()
    api.pageView = vi.fn(async () => Promise.reject(new Error('x')))
    start(api)
    await tick()
    await tick(16000)
    expect(api.pageView).toHaveBeenCalled()
    content.destroy()
    content = null
    delete api.pageView
    clientRender()
    window.history.replaceState(null, '', '/patent/US1000000B2/en')
    start(api)
    await tick()
    await tick(16000)
    expect(document.querySelector('[data-patent-assist]')).not.toBeNull()
  })

  it('ratings and re-asks still work with the tracker running', async () => {
    clientRender()
    const api = apiMock()
    start(api)
    await tick()
    const shadow = content.widget.shadow
    const flush = async () => {
      for (let i = 0; i < 10; i += 1) await Promise.resolve()
    }
    const ask = async (text) => {
      shadow.querySelector('textarea').value = text
      shadow.querySelector('button[data-action="send"]').click()
      await flush()
    }
    await ask('what is claim 1 about')
    expect(shadow.querySelector('button[data-action="up"]')).toBeNull() // the rating request waits for the third answer
    await tick(1000)
    await ask('what is claim 1 about, rephrased')
    await flush()
    expect(api.reask).toHaveBeenCalledTimes(1)
    await tick(1000)
    await ask('what is claim 1 about, once more')
    shadow.querySelector('button[data-action="up"]').click()
    await flush()
    expect(api.rating).toHaveBeenCalledTimes(1)
    expect(api.rating.mock.calls[0][0]).toMatchObject({ question_id: 'q1', rating: 'up' })
    expect(api.reask).toHaveBeenCalledTimes(2)
    expect(api.reask.mock.calls[0][0].question_id).toBe('q1')
  })
})

describe('final fix wave (M9)', () => {
  it('a backwards clock step does not stop accrual: the clock is rebased on the new value', async () => {
    const tracker = make()
    await advance(2000)
    expect(tracker.snapshot().dwell_ms).toBe(2000)
    t = 500 // the wall clock stepped back 1500 ms
    expect(tracker.snapshot().dwell_ms).toBe(2000) // nothing is added for the step itself
    t = 3500 // three real seconds later
    expect(tracker.snapshot().dwell_ms).toBe(5000)
  })

  it('every flush payload (pagehide, tab hidden, destroy, interval) has exactly the five keys', async () => {
    const els = buildPage()
    const tracker = make()
    setRatio(els.claims, 0.9)
    await advance(16000) // interval flush
    window.dispatchEvent(new Event('pagehide'))
    await advance(2000)
    setVisibility('hidden')
    await advance(2000)
    setVisibility('visible')
    await advance(2000)
    tracker.destroy()
    await flushMicrotasks()
    expect(send.mock.calls.length).toBeGreaterThanOrEqual(3)
    for (const [payload] of send.mock.calls) {
      expect(Object.keys(payload).sort()).toEqual(KEYS)
      expect(Object.keys(payload.sections).sort()).toEqual([...SECTION_NAMES].sort())
    }
  })

  it('a request that never settles is given up after 30 s, so later heartbeats are sent again', async () => {
    send = vi.fn(() => new Promise(() => {}))
    make()
    await advance(15000)
    expect(send).toHaveBeenCalledTimes(1)
    await advance(15000) // the first request is still pending: this heartbeat waits
    expect(send).toHaveBeenCalledTimes(1)
    await advance(15000) // 30 s after the first send: it times out and the waiting totals go out
    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls[1][0].dwell_ms).toBe(45000)
  })

  it('a timed-out request that settles late changes nothing', async () => {
    let resolveFirst
    send = vi.fn()
    send.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
    send.mockImplementation(async () => ({ ok: true }))
    make()
    await advance(45000)
    expect(send).toHaveBeenCalledTimes(2)
    resolveFirst({ ok: true })
    await flushMicrotasks()
    await advance(15000)
    expect(send).toHaveBeenCalledTimes(3) // one per interval, no duplicate from the late settle
  })

  it('a ratio a hair under 0.4 (float rounding) still counts as visible; clearly under does not', async () => {
    const els = buildPage()
    const tracker = make()
    setRatio(els.claims, 0.39999999)
    await advance(3000)
    expect(tracker.snapshot().sections.claims).toBe(3000)
    setRatio(els.claims, 0.399)
    await advance(2000)
    expect(tracker.snapshot().sections.claims).toBe(3000)
  })
})

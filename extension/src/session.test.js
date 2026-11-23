import { JSDOM } from 'jsdom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { checkReady, createSessionController, isPatentRoute, patentKeyFromUrl } from './session.js'

const START = '/patent/US1000000B2/en'

/** A minimal Google Patents result: pubnum, title, claims with [num]. */
function render(doc, { number = 'US1000000B2', title = 'A title', claims = 2 } = {}) {
  const claimHtml = Array.from({ length: claims }, (_, i) => `<div class="claim" num="0000${i + 1}">${i + 1}. A thing.</div>`).join('')
  doc.body.innerHTML = `<patent-result><section class="knowledge-card"><header>
    <h2 id="pubnum">${number}</h2><h1 id="title">${title}</h1></header></section>
    <patent-text name="claims"><section class="flex"><div class="claims">${claimHtml}</div></section></patent-text></patent-result>`
}

// pushState without our wrapper: what the page's own scripts do (a different JS world).
const nativePush = (path) => History.prototype.pushState.call(window.history, null, '', path)

const ctls = []
function make(opts = {}) {
  const onSession = vi.fn()
  const onTeardown = vi.fn()
  const extract = vi.fn((_doc, url) => ({ patent_number: patentKeyFromUrl(url), claims: [{ number: 1 }], completeness: {} }))
  const ctl = createSessionController({ win: window, extract, onSession, onTeardown, ...opts })
  ctls.push(ctl)
  return { ctl, onSession, onTeardown, extract }
}
const tick = (ms = 500) => vi.advanceTimersByTimeAsync(ms)

beforeEach(() => {
  vi.useFakeTimers()
  window.history.replaceState(null, '', START)
  document.body.innerHTML = ''
})
afterEach(() => {
  while (ctls.length) ctls.pop().destroy()
  vi.useRealTimers()
})

describe('isPatentRoute', () => {
  it.each([
    ['https://patents.google.com/patent/US10000000B2/en', true],
    ['https://patents.google.com/patent/US10000000B2', true],
    ['https://patents.google.com/patent/US10000000B2/en?oq=x#y', true],
    ['https://patents.google.com/patent/EP4517554A1/en', true],
    ['https://patents.google.com/patent/', false],
    ['https://patents.google.com/patent', false],
    ['https://patents.google.com/', false],
    ['https://patents.google.com/?q=thermostat&oq=thermostat', false],
    ['https://patents.google.com/patents/US1', false],
    ['https://patents.google.com/xhr/patent/US1', false],
    ['http://patents.google.com/patent/US1B2/en', false],
    ['https://patents.google.com:8443/patent/US1B2/en', false],
    ['https://patents.google.com.evil.com/patent/US1B2/en', false],
    ['https://evilpatents.google.com/patent/US1B2/en', false],
    ['https://www.google.com/patent/US1B2/en', false],
    ['https://example.com/patent/US1B2/en', false],
    ['not a url', false],
    ['', false],
    [undefined, false],
    [null, false],
  ])('%s -> %s', (url, expected) => {
    expect(isPatentRoute(url)).toBe(expected)
  })
  it('accepts a URL object', () => {
    expect(isPatentRoute(new URL('https://patents.google.com/patent/US1B2/en'))).toBe(true)
  })
})

describe('patentKeyFromUrl', () => {
  it('normalises the number from the path', () => {
    expect(patentKeyFromUrl('https://patents.google.com/patent/us10000000b2/en')).toBe('US10000000B2')
    expect(patentKeyFromUrl('https://patents.google.com/patent/US%2010,000,000%20B2/en')).toBe('US10000000B2')
    expect(patentKeyFromUrl('https://patents.google.com/?q=x')).toBe('')
  })
})

describe('checkReady', () => {
  const url = (n) => `https://patents.google.com/patent/${n}/en`
  it('is ready when number, title and claims are present', () => {
    render(document)
    expect(checkReady(document, url('US1000000B2')).ok).toBe(true)
  })
  it('accepts a URL number without kind code that the page extends', () => {
    render(document, { number: 'US7629993B2' })
    expect(checkReady(document, url('US7629993')).ok).toBe(true)
  })
  it('does not accept a different number, even a longer one with a kind code in the URL', () => {
    render(document, { number: 'US1000000B2' })
    expect(checkReady(document, url('US1000000B1')).ok).toBe(false)
    expect(checkReady(document, url('US2000000B2')).ok).toBe(false)
    // US100000 is a prefix of US1000000B2 but is a different number only when it has a kind code
    expect(checkReady(document, url('US100000B2')).ok).toBe(false)
  })
  it('a kindless URL only accepts a page whose remainder is a kind code (no longer number)', () => {
    render(document, { number: 'US6756997B1' })
    // /patent/US675699 is a prefix of the previous page's number, but "7B1" is not a kind code
    expect(checkReady(document, url('US675699')).ok).toBe(false)
    expect(checkReady(document, url('US6756')).ok).toBe(false)
    expect(checkReady(document, url('US6756997')).ok).toBe(true) // remainder "B1"
    render(document, { number: 'US7629993A' })
    expect(checkReady(document, url('US7629993')).ok).toBe(true) // remainder "A"
    render(document, { number: 'US7629993' })
    expect(checkReady(document, url('US7629993')).ok).toBe(true) // exact
  })
  it('needs a title and claims', () => {
    render(document, { title: '   ' })
    expect(checkReady(document, url('US1000000B2'))).toMatchObject({ ok: false, onlyClaimsMissing: false })
    render(document, { claims: 0 })
    expect(checkReady(document, url('US1000000B2'))).toMatchObject({ ok: false, onlyClaimsMissing: true })
  })
  it('reads the page number with spaces and commas', () => {
    render(document, { number: 'US 1,000,000 B2' })
    expect(checkReady(document, url('US1000000B2')).ok).toBe(true)
  })
})

describe('createSessionController', () => {
  it('starts one session on a patent route once the page is ready', async () => {
    render(document)
    const { onSession, extract } = make()
    expect(onSession).not.toHaveBeenCalled()
    await tick()
    expect(onSession).toHaveBeenCalledTimes(1)
    expect(extract).toHaveBeenCalledTimes(1)
    expect(extract.mock.calls[0][0]).toBe(document)
    const session = onSession.mock.calls[0][0]
    expect(session.id).toMatch(/^[A-Za-z0-9-]{8,64}$/)
    expect(session.patentNumber).toBe('US1000000B2')
    expect(session.brief.patent_number).toBe('US1000000B2')
    expect(typeof session.onDestroy).toBe('function')
    await tick(5000)
    expect(onSession).toHaveBeenCalledTimes(1)
  })

  it('does nothing on a search route (no session, no extraction, nothing sent)', async () => {
    window.history.replaceState(null, '', '/?q=thermostat')
    render(document)
    const { onSession, extract, onTeardown } = make()
    await tick(3000)
    expect(onSession).not.toHaveBeenCalled()
    expect(extract).not.toHaveBeenCalled()
    expect(onTeardown).not.toHaveBeenCalled()
  })

  it('does nothing on another origin', async () => {
    const dom = new JSDOM('<body></body>', { url: 'https://example.com/patent/US1000000B2/en', pretendToBeVisual: true })
    render(dom.window.document)
    const onSession = vi.fn()
    const extract = vi.fn()
    const ctl = createSessionController({ win: dom.window, extract, onSession, onTeardown: vi.fn() })
    await tick(3000)
    ctl.destroy()
    expect(onSession).not.toHaveBeenCalled()
    expect(extract).not.toHaveBeenCalled()
    dom.window.close()
  })

  it('does not start from stale DOM after pushState to another patent, only once the new page is ready', async () => {
    render(document, { number: 'US1000000B2' })
    const { onSession, onTeardown, extract } = make()
    await tick()
    expect(onSession).toHaveBeenCalledTimes(1)
    const first = onSession.mock.calls[0][0]

    window.history.pushState(null, '', '/patent/US2000000B2/en') // DOM still shows US1000000B2
    expect(onTeardown).toHaveBeenCalledTimes(1) // the old session is gone at once
    await tick(3000)
    expect(onSession).toHaveBeenCalledTimes(1)
    expect(extract).toHaveBeenCalledTimes(1)

    render(document, { number: 'US2000000B2' })
    await tick()
    expect(onSession).toHaveBeenCalledTimes(2)
    const second = onSession.mock.calls[1][0]
    expect(second.id).not.toBe(first.id)
    expect(second.patentNumber).toBe('US2000000B2')
    expect(extract).toHaveBeenCalledTimes(2)
  })

  it('does not start a new session for the same patent (query or replaceState)', async () => {
    render(document)
    const { onSession, onTeardown } = make()
    await tick()
    window.history.replaceState(null, '', `${START}?oq=thermostat`)
    window.history.pushState(null, '', `${START}#claims`)
    await tick(3000)
    expect(onSession).toHaveBeenCalledTimes(1)
    expect(onTeardown).not.toHaveBeenCalled()
  })

  it('tears down on a search route and starts again on the next patent', async () => {
    render(document)
    const { onSession, onTeardown, extract } = make()
    await tick()
    window.history.pushState(null, '', '/?q=x')
    expect(onTeardown).toHaveBeenCalledTimes(1)
    await tick(3000)
    expect(extract).toHaveBeenCalledTimes(1)
    window.history.pushState(null, '', '/patent/US3000000B2/en')
    render(document, { number: 'US3000000B2' })
    await tick()
    expect(onSession).toHaveBeenCalledTimes(2)
  })

  it('runs the registered destroy callbacks on teardown (the tracker hook)', async () => {
    render(document)
    const { onSession } = make()
    await tick()
    const fn = vi.fn()
    onSession.mock.calls[0][0].onDestroy(fn)
    window.history.pushState(null, '', '/?q=x')
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('a throwing destroy callback or onTeardown does not break the controller', async () => {
    render(document)
    const { onSession, onTeardown } = make()
    onTeardown.mockImplementation(() => {
      throw new Error('boom')
    })
    await tick()
    onSession.mock.calls[0][0].onDestroy(() => {
      throw new Error('boom')
    })
    window.history.pushState(null, '', '/patent/US2000000B2/en')
    render(document, { number: 'US2000000B2' })
    await tick()
    expect(onSession).toHaveBeenCalledTimes(2)
  })

  it('detects a navigation made by the page itself (no wrapper) through popstate', async () => {
    render(document)
    const { onTeardown } = make()
    await tick()
    nativePush('/patent/US2000000B2/en')
    expect(onTeardown).not.toHaveBeenCalled()
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(onTeardown).toHaveBeenCalledTimes(1)
  })

  it('detects a navigation made by the page itself through DOM mutations', async () => {
    render(document)
    const { onSession, onTeardown } = make()
    await tick()
    nativePush('/patent/US2000000B2/en')
    render(document, { number: 'US2000000B2' })
    await tick()
    expect(onTeardown).toHaveBeenCalledTimes(1)
    expect(onSession).toHaveBeenCalledTimes(2)
  })

  it('waits for the page: number mismatch, empty title, missing claims', async () => {
    const { onSession, extract } = make()
    render(document, { number: 'US9999999B2' })
    await tick(2000)
    render(document, { title: '' })
    await tick(2000)
    render(document, { claims: 0 })
    await tick(2000)
    expect(extract).not.toHaveBeenCalled()
    render(document)
    await tick()
    expect(onSession).toHaveBeenCalledTimes(1)
  })

  it('accepts a page with no claims only after a grace period', async () => {
    render(document, { claims: 0 })
    const { onSession } = make({ claimsGraceMs: 8000 })
    await tick(5000)
    expect(onSession).not.toHaveBeenCalled()
    await tick(4000)
    expect(onSession).toHaveBeenCalledTimes(1)
  })

  it('never starts a session whose brief has no patent number', async () => {
    render(document)
    const extract = vi.fn(() => ({ patent_number: '', claims: [] }))
    const { onSession } = make({ extract })
    await tick(3000)
    expect(extract).toHaveBeenCalled()
    expect(onSession).not.toHaveBeenCalled()
  })

  it('does not start when extraction throws, and retries on the next change', async () => {
    render(document)
    let fail = true
    const extract = vi.fn((_d, url) => {
      if (fail) throw new Error('bad dom')
      return { patent_number: patentKeyFromUrl(url), claims: [{ number: 1 }] }
    })
    const { onSession } = make({ extract })
    await tick()
    expect(onSession).not.toHaveBeenCalled()
    fail = false
    document.body.appendChild(document.createElement('div'))
    await tick()
    expect(onSession).toHaveBeenCalledTimes(1)
  })

  it('debounces bursts of mutations into one check, with a maximum wait', async () => {
    render(document)
    const extract = vi.fn(() => ({ patent_number: '', claims: [] }))
    make({ extract })
    for (let i = 0; i < 30; i += 1) {
      document.body.appendChild(document.createElement('i'))
      await tick(100) // never quiet for the debounce window
    }
    expect(extract.mock.calls.length).toBeGreaterThanOrEqual(1) // max wait forced a check
    expect(extract.mock.calls.length).toBeLessThan(6)
  })

  it('destroy stops everything: no observers, no timers, history restored', async () => {
    const proto = History.prototype
    const before = window.history.pushState
    render(document)
    const { ctl, onSession, onTeardown, extract } = make()
    expect(window.history.pushState).not.toBe(before) // wrapped while active
    await tick()
    expect(onSession).toHaveBeenCalledTimes(1)
    ctl.destroy()
    expect(onTeardown).toHaveBeenCalledTimes(1)
    expect(window.history.pushState).toBe(before)
    expect(Object.prototype.hasOwnProperty.call(window.history, 'pushState')).toBe(false)
    expect(proto.pushState).toBe(before)
    window.history.pushState(null, '', '/patent/US2000000B2/en')
    render(document, { number: 'US2000000B2' })
    document.body.appendChild(document.createElement('div'))
    window.dispatchEvent(new PopStateEvent('popstate'))
    await tick(5000)
    expect(onSession).toHaveBeenCalledTimes(1)
    expect(extract).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
    ctl.destroy() // idempotent
    expect(onTeardown).toHaveBeenCalledTimes(1)
  })

  it('destroy before the first check cancels the pending timer', async () => {
    render(document)
    const { ctl, extract } = make()
    ctl.destroy()
    await tick(5000)
    expect(extract).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })
})

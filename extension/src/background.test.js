import { describe, expect, it, vi } from 'vitest'
import { createMessageHandler, registerListener } from './background.js'
import { MESSAGES, isFixedMessage } from './messages.js'

const ORIGIN = 'https://assist.example.com'
const KEY = 'sekret-key-123'

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}
function handler(fetchFn, overrides = {}) {
  return createMessageHandler({ key: KEY, origin: ORIGIN, fetchFn, ...overrides })
}
const CHAT = { answer: 'a', question_id: 'q1', intent: 'other', assist_version: 1, citation_check: { ok: true } }

describe('createMessageHandler', () => {
  it('posts to the right endpoint with the assist key and returns the parsed JSON', async () => {
    const fetchFn = vi.fn(async () => json(CHAT))
    const out = await handler(fetchFn)({ type: 'chat', payload: { question: 'hi' } })
    expect(out).toEqual(CHAT)
    const [url, init] = fetchFn.mock.calls[0]
    expect(url).toBe(`${ORIGIN}/assist/chat`)
    expect(init.method).toBe('POST')
    expect(init.headers['X-Assist-Key']).toBe(KEY)
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual({ question: 'hi' })
    expect(init.credentials).toBe('omit')
    expect(init.redirect).toBe('error')
  })

  it.each([
    ['rating', '/assist/ratings'],
    ['reask', '/assist/reasks'],
    ['page_view', '/assist/page-views'],
  ])('routes %s to %s and treats 204 as success', async (type, path) => {
    const fetchFn = vi.fn(async () => new Response(null, { status: 204 }))
    const out = await handler(fetchFn)({ type, payload: { a: 1 } })
    expect(out).toEqual({ ok: true })
    expect(fetchFn.mock.calls[0][0]).toBe(`${ORIGIN}${path}`)
    expect(fetchFn.mock.calls[0][1].headers['X-Assist-Key']).toBe(KEY)
  })

  it('rejects unknown types and bad payloads without calling the backend', async () => {
    const fetchFn = vi.fn()
    const h = handler(fetchFn)
    for (const msg of [null, undefined, 'chat', {}, { type: 'nope', payload: {} }, { type: 'chat' }, { type: 'chat', payload: [] }, { type: 'chat', payload: 'x' }, { type: '__proto__', payload: {} }]) {
      const out = await h(msg)
      expect(out).toEqual({ ok: false, status: 400, message: MESSAGES.badRequest })
    }
    expect(fetchFn).not.toHaveBeenCalled()
  })

  it('is not configured without a key: fixed text, no request', async () => {
    const fetchFn = vi.fn()
    for (const key of ['', undefined, '   ']) {
      const out = await handler(fetchFn, { key })({ type: 'chat', payload: { question: 'hi' } })
      expect(out).toEqual({ ok: false, status: 503, message: 'Assist is not configured.' })
    }
    expect(fetchFn).not.toHaveBeenCalled()
  })

  it('maps every error to a fixed object and never leaks provider text or the key', async () => {
    const leaky = { detail: `provider said: ${KEY} secret traceback`, message: 'GROQ failure' }
    for (const status of [400, 401, 403, 404, 409, 422, 500, 502, 503]) {
      const out = await handler(async () => json(leaky, status))({ type: 'chat', payload: { question: 'hi' } })
      expect(out).toEqual({ ok: false, status, message: MESSAGES.generic })
      expect(JSON.stringify(out)).not.toContain(KEY)
      expect(JSON.stringify(out)).not.toContain('provider')
    }
    expect(MESSAGES.generic).toBe('The assistant could not answer right now. Please try again.')
    const limited = await handler(async () => json(leaky, 429))({ type: 'chat', payload: { question: 'hi' } })
    expect(limited).toEqual({ ok: false, status: 429, message: MESSAGES.rateLimited })
  })

  it('network failures, timeouts and malformed bodies become the fixed error', async () => {
    const boom = await handler(async () => {
      throw new Error(`connect to ${ORIGIN} with ${KEY} failed`)
    })({ type: 'chat', payload: { question: 'hi' } })
    expect(boom).toEqual({ ok: false, status: 0, message: MESSAGES.generic })
    const notJson = await handler(async () => new Response('<html>', { status: 200 }))({ type: 'chat', payload: { question: 'hi' } })
    expect(notJson).toEqual({ ok: false, status: 502, message: MESSAGES.generic })
    const wrongShape = await handler(async () => json({ answer: 5 }))({ type: 'chat', payload: { question: 'hi' } })
    expect(wrongShape).toEqual({ ok: false, status: 502, message: MESSAGES.generic })
  })

  it('aborts a request that takes too long', async () => {
    vi.useFakeTimers()
    try {
      const fetchFn = vi.fn((_url, init) => new Promise((_res, rej) => init.signal.addEventListener('abort', () => rej(new Error('aborted')))))
      const pending = handler(fetchFn, { timeoutMs: 1000 })({ type: 'chat', payload: { question: 'hi' } })
      await vi.advanceTimersByTimeAsync(1500)
      expect(await pending).toEqual({ ok: false, status: 0, message: MESSAGES.generic })
    } finally {
      vi.useRealTimers()
    }
  })

  it('an unusable key (header rejects it) gives the fixed error, not the key', async () => {
    const fetchFn = vi.fn(async () => {
      throw new TypeError(`Failed to set header ${KEY}`)
    })
    const out = await handler(fetchFn)({ type: 'chat', payload: { question: 'x' } })
    expect(JSON.stringify(out)).not.toContain(KEY)
    expect(out.ok).toBe(false)
  })

  it('every fixed message is recognised as fixed', () => {
    for (const text of Object.values(MESSAGES)) expect(isFixedMessage(text)).toBe(true)
    expect(isFixedMessage('anything else')).toBe(false)
  })
})

describe('registerListener', () => {
  function fakeChrome(id = 'ext-id') {
    const listeners = []
    return { listeners, runtime: { id, onMessage: { addListener: (fn) => listeners.push(fn) } } }
  }
  it('answers asynchronously (returns true) and sends the handler result', async () => {
    const chrome = fakeChrome()
    const handle = vi.fn(async () => ({ ok: true }))
    registerListener(chrome, handle)
    const sendResponse = vi.fn()
    const ret = chrome.listeners[0]({ type: 'rating', payload: {} }, { id: 'ext-id' }, sendResponse)
    expect(ret).toBe(true)
    await Promise.resolve()
    await Promise.resolve()
    expect(sendResponse).toHaveBeenCalledWith({ ok: true })
  })
  it('ignores messages from other extensions', () => {
    const chrome = fakeChrome()
    const handle = vi.fn()
    registerListener(chrome, handle)
    const sendResponse = vi.fn()
    expect(chrome.listeners[0]({ type: 'chat', payload: {} }, { id: 'someone-else' }, sendResponse)).toBe(false)
    expect(handle).not.toHaveBeenCalled()
  })
  it('does nothing without chrome.runtime.onMessage', () => {
    expect(() => registerListener(undefined, vi.fn())).not.toThrow()
    expect(() => registerListener({}, vi.fn())).not.toThrow()
  })
  it('a handler that throws still answers with the fixed error', async () => {
    const chrome = fakeChrome()
    registerListener(chrome, async () => {
      throw new Error('x')
    })
    const sendResponse = vi.fn()
    chrome.listeners[0]({ type: 'chat', payload: {} }, { id: 'ext-id' }, sendResponse)
    await new Promise((r) => setTimeout(r, 0))
    expect(sendResponse).toHaveBeenCalledWith({ ok: false, status: 0, message: MESSAGES.generic })
  })
})

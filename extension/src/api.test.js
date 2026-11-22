import { describe, expect, it, vi } from 'vitest'
import { createApi } from './api.js'
import { MESSAGES } from './messages.js'

const FAIL = { ok: false, status: 0, message: MESSAGES.generic }

function runtime(reply, { lastError } = {}) {
  const rt = {
    lastError: undefined,
    sendMessage: vi.fn((_msg, cb) => {
      rt.lastError = lastError
      cb(reply)
      rt.lastError = undefined
    }),
  }
  return rt
}

describe('createApi', () => {
  it('sends typed messages to the background and resolves its reply', async () => {
    const rt = runtime({ answer: 'x' })
    const api = createApi(() => rt)
    await expect(api.chat({ question: 'hi' })).resolves.toEqual({ answer: 'x' })
    expect(rt.sendMessage.mock.calls[0][0]).toEqual({ type: 'chat', payload: { question: 'hi' } })
    await api.rating({ a: 1 })
    await api.reask({ b: 2 })
    expect(rt.sendMessage.mock.calls.map((c) => c[0].type)).toEqual(['chat', 'rating', 'reask'])
  })
  it('turns lastError, no reply, a throw and a missing runtime into the fixed error', async () => {
    await expect(createApi(() => runtime(undefined, { lastError: { message: 'boom' } })).chat({})).resolves.toEqual(FAIL)
    await expect(createApi(() => runtime(undefined)).chat({})).resolves.toEqual(FAIL)
    await expect(createApi(() => ({ sendMessage: () => { throw new Error('Extension context invalidated.') } })).chat({})).resolves.toEqual(FAIL)
    await expect(createApi(() => undefined).chat({})).resolves.toEqual(FAIL)
  })
})

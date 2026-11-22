import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { extractBrief } from './extractor.js'
import { startContent } from './wiring.js'
import { load } from './testutil.js'

const apiMock = () => ({
  chat: vi.fn(async () => ({ answer: 'ok', question_id: 'q1', intent: 'other' })),
  rating: vi.fn(async () => ({})),
  reask: vi.fn(async () => ({})),
})
const tick = (ms = 500) => vi.advanceTimersByTimeAsync(ms)
const flush = async () => {
  for (let i = 0; i < 10; i += 1) await Promise.resolve()
}

let content
beforeEach(() => {
  vi.useFakeTimers()
  document.body.innerHTML = ''
})
afterEach(() => {
  content?.destroy()
  content = null
  vi.useRealTimers()
})

function showFixture(name) {
  const { doc } = load(name)
  document.body.innerHTML = doc.body.innerHTML
}

// Real saved pages: parsing and extracting them in jsdom takes a few seconds.
describe('startContent with a real saved page', { timeout: 60000 }, () => {
  it('shows the widget for a US patent page and sends the extracted brief with the question', async () => {
    window.history.replaceState(null, '', '/patent/US6756997B1/en')
    showFixture('us6756997b1.html')
    const api = apiMock()
    const hooks = []
    content = startContent({ win: window, api, extract: extractBrief, onSession: (s) => hooks.push(s) })
    await tick()
    const host = document.querySelector('[data-patent-assist]')
    expect(host).not.toBeNull()
    expect(hooks).toHaveLength(1)
    expect(hooks[0].patentNumber).toBe('US6756997B1')

    const shadow = content.widget.shadow
    shadow.querySelector('.launcher').click()
    shadow.querySelector('textarea').value = 'What is claim 1?'
    shadow.querySelector('button[data-action="send"]').click()
    await flush()
    const body = api.chat.mock.calls[0][0]
    expect(body.session_id).toBe(hooks[0].id)
    expect(body.brief.patent_number).toBe('US6756997B1')
    expect(body.brief.claims.length).toBeGreaterThan(0)
    expect(body.question).toBe('What is claim 1?')
    expect(body.history).toEqual([])
  })

  it('removes the widget and sends nothing on a search route; a new patent gets a new session with an empty history', async () => {
    window.history.replaceState(null, '', '/patent/US6756997B1/en')
    showFixture('us6756997b1.html')
    const api = apiMock()
    const hooks = []
    content = startContent({ win: window, api, extract: extractBrief, onSession: (s) => hooks.push(s) })
    await tick()
    let shadow = content.widget.shadow
    shadow.querySelector('textarea').value = 'first'
    shadow.querySelector('button[data-action="send"]').click()
    await flush()
    expect(api.chat).toHaveBeenCalledTimes(1)

    window.history.pushState(null, '', '/?q=thermostat')
    await tick(3000)
    expect(document.querySelector('[data-patent-assist]')).toBeNull()

    window.history.pushState(null, '', '/patent/US12401720B1/en')
    showFixture('us12401720b1.html')
    await tick()
    expect(hooks).toHaveLength(2)
    expect(hooks[1].id).not.toBe(hooks[0].id)
    shadow = content.widget.shadow
    shadow.querySelector('textarea').value = 'second'
    shadow.querySelector('button[data-action="send"]').click()
    await flush()
    const body = api.chat.mock.calls[1][0]
    expect(body.history).toEqual([])
    expect(body.session_id).toBe(hooks[1].id)
    expect(body.brief.patent_number).toBe('US12401720B1')
    expect(api.chat).toHaveBeenCalledTimes(2)
  })

  it('destroy removes the widget', async () => {
    window.history.replaceState(null, '', '/patent/US6756997B1/en')
    showFixture('us6756997b1.html')
    content = startContent({ win: window, api: apiMock(), extract: extractBrief })
    await tick()
    expect(document.querySelector('[data-patent-assist]')).not.toBeNull()
    content.destroy()
    expect(document.querySelector('[data-patent-assist]')).toBeNull()
  })
})

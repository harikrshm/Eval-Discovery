// Service worker: the only code that talks to the backend. The assist key is inlined here at
// build time (VITE_ASSIST_KEY, see vite.config.js) and is never sent to a page. The content script
// sends {type, payload}; this answers with the parsed JSON, or a FIXED error object
// {ok:false, status, message}: never provider text and never the key.
import { MESSAGES, failure } from './messages.js'

// Replaced by Vite `define` in the background build only; absent (and so empty) everywhere else.
/* global __PA_ASSIST_KEY__, __PA_BACKEND_ORIGIN__ */
const BUILD_KEY = typeof __PA_ASSIST_KEY__ === 'string' ? __PA_ASSIST_KEY__ : ''
const BUILD_ORIGIN = typeof __PA_BACKEND_ORIGIN__ === 'string' ? __PA_BACKEND_ORIGIN__ : ''

const ROUTES = {
  chat: '/assist/chat',
  rating: '/assist/ratings',
  reask: '/assist/reasks',
  page_view: '/assist/page-views',
}
const TIMEOUT_MS = 60000
// A page-view or chat payload is a brief (at most about 540 KB from the extractor) plus a short history, so
// nothing legitimate comes near this; a bigger one is not forwarded.
export const MAX_PAYLOAD_CHARS = 1_000_000
const CONTENT_ORIGIN = 'https://patents.google.com'
const CONTENT_PATH = '/patent/'

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

function validChat(data) {
  return isPlainObject(data) && typeof data.answer === 'string' && typeof data.question_id === 'string' && typeof data.intent === 'string'
}

/**
 * @param {{key?: string, origin: string, fetchFn?: typeof fetch, timeoutMs?: number}} cfg
 * @returns {(message: unknown) => Promise<object>} never rejects
 */
export function createMessageHandler({ key, origin, fetchFn = (...a) => fetch(...a), timeoutMs = TIMEOUT_MS }) {
  const assistKey = typeof key === 'string' ? key.trim() : ''
  return async function handle(message) {
    const type = isPlainObject(message) ? message.type : undefined
    if (typeof type !== 'string' || !Object.hasOwn(ROUTES, type) || !isPlainObject(message.payload)) {
      return failure(400, MESSAGES.badRequest)
    }
    if (!assistKey) return failure(503, MESSAGES.notConfigured)
    let body
    try {
      body = JSON.stringify(message.payload)
    } catch {
      return failure(400, MESSAGES.badRequest)
    }
    if (typeof body !== 'string' || body.length > MAX_PAYLOAD_CHARS) return failure(400, MESSAGES.badRequest)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const res = await fetchFn(`${origin}${ROUTES[type]}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Assist-Key': assistKey },
        body,
        credentials: 'omit',
        redirect: 'error',
        signal: controller.signal,
      })
      if (res.status === 429) return failure(429, MESSAGES.rateLimited)
      if (!res.ok) return failure(res.status, MESSAGES.generic)
      if (res.status === 204) return { ok: true }
      let data
      try {
        data = await res.json()
      } catch {
        return failure(502, MESSAGES.generic)
      }
      if (type === 'chat' && !validChat(data)) return failure(502, MESSAGES.generic)
      return data
    } catch {
      return failure(0, MESSAGES.generic)
    } finally {
      clearTimeout(timer)
    }
  }
}

/** True when the sender is a page of https://patents.google.com/patent/... (where the content script runs). */
export function isPatentPageSender(sender) {
  const raw = sender && (sender.url || (sender.tab && sender.tab.url))
  if (typeof raw !== 'string') return false
  try {
    const u = new URL(raw)
    return u.origin === CONTENT_ORIGIN && u.pathname.startsWith(CONTENT_PATH)
  } catch {
    return false
  }
}

/**
 * Register the onMessage listener. Only messages from this extension are answered, and of those only
 * the ones sent from a Google Patents patent page (the content script); any other sender of this
 * extension gets one fixed error and nothing is forwarded.
 */
export function registerListener(chromeApi, handle) {
  const runtime = chromeApi && chromeApi.runtime
  if (!runtime || !runtime.onMessage) return
  runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!sender || sender.id !== runtime.id) return false
    if (!isPatentPageSender(sender)) {
      sendResponse(failure(403, MESSAGES.badRequest))
      return false
    }
    handle(message).then(sendResponse, () => sendResponse(failure(0, MESSAGES.generic)))
    return true // the response is sent asynchronously
  })
}

registerListener(
  typeof chrome === 'undefined' ? undefined : chrome,
  createMessageHandler({ key: BUILD_KEY, origin: BUILD_ORIGIN }),
)

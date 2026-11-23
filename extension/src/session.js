// Session handling for Google Patents, which is a single-page app: the page can show another
// patent without a reload and old content can stay in the DOM for a moment. A session is one
// patent page view: fresh id, empty history, a freshly extracted brief. Nothing is read, recorded
// or sent on a route that is not /patent/... on https://patents.google.com.
import { normalizePatentNumber } from './claimrefs.js'

const PATENTS_ORIGIN = 'https://patents.google.com'
const PATENT_PREFIX = '/patent/'

function parse(url) {
  try {
    return new URL(String(url))
  } catch {
    return null
  }
}

/** True only for https://patents.google.com/patent/<number>[/...] */
export function isPatentRoute(url) {
  if (url === undefined || url === null || url === '') return false
  const u = parse(url)
  if (!u || u.origin !== PATENTS_ORIGIN || !u.pathname.startsWith(PATENT_PREFIX)) return false
  return u.pathname.slice(PATENT_PREFIX.length).split('/')[0] !== ''
}

/** The normalised patent number in the path ("US10000000B2"), or "" off a patent route. */
export function patentKeyFromUrl(url) {
  if (!isPatentRoute(url)) return ''
  const segment = parse(url).pathname.slice(PATENT_PREFIX.length).split('/')[0]
  let decoded = segment
  try {
    decoded = decodeURIComponent(segment)
  } catch {
    // keep the raw segment
  }
  return normalizePatentNumber(decoded)
}

const HAS_KIND_CODE = /^[A-Z]{2}\d+[A-Z]\d?$/
// What a kindless URL number may be extended by on the page: a kind code such as B2 or A (not more digits).
const KIND_TAIL = /^[A-Z]\d?$/

/**
 * Whether the DOM shows the patent of the URL (not a previous one) and is complete enough to read:
 * h2#pubnum equals the URL number (or extends it when the URL has no kind code), h1#title has
 * text and the claims have [num] elements. `onlyClaimsMissing` lets the caller give a page with
 * no claims a grace period.
 */
export function checkReady(doc, url) {
  const key = patentKeyFromUrl(url)
  const page = normalizePatentNumber(doc.querySelector('h2#pubnum')?.textContent ?? '')
  const numberOk =
    Boolean(key) && Boolean(page) && (page === key || (!HAS_KIND_CODE.test(key) && page.startsWith(key) && KIND_TAIL.test(page.slice(key.length))))
  const titleOk = (doc.querySelector('h1#title')?.textContent ?? '').trim() !== ''
  const claimsOk = doc.querySelector('patent-text[name="claims"] [num]') !== null
  return {
    ok: numberOk && titleOk && claimsOk,
    key,
    onlyClaimsMissing: numberOk && titleOk && !claimsOk,
  }
}

function newSessionId(win) {
  const c = win.crypto ?? globalThis.crypto
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  const bytes = new Uint8Array(16)
  c.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/**
 * Watch the page and call onSession(session) once per patent page view, onTeardown(session) when it
 * ends (another patent, another route, destroy). A session is
 * {id, key, patentNumber, brief, onDestroy(fn)}; the functions given to onDestroy run on teardown
 * (the hook for the tracker).
 *
 * Route changes are detected three ways because a content script lives in an isolated JS world and
 * does not see the page's own history.pushState calls: wrapped history methods (calls made from
 * this world), popstate / navigation events, and a debounced MutationObserver on the document,
 * since a client-side navigation always changes the DOM.
 *
 * @returns {{destroy: () => void}}
 */
export function createSessionController({
  win,
  extract,
  onSession,
  onTeardown,
  debounceMs = 400,
  maxWaitMs = 2000,
  claimsGraceMs = 8000,
}) {
  const doc = win.document
  let destroyed = false
  let current = null
  let timer = null
  let timerStartedAt = 0
  let lastHref = String(win.location.href)
  let claimsWaitStart = null // {key, at}

  const safely = (fn, ...args) => {
    try {
      return fn?.(...args)
    } catch {
      return undefined // a failing hook must not stop the controller
    }
  }

  function teardown() {
    const session = current
    if (!session) return
    current = null
    for (const fn of session.destroyFns.splice(0)) safely(fn)
    safely(onTeardown, session)
  }

  /** Cheap: compares the URL and ends a session that no longer matches the route. */
  function routeCheck() {
    if (destroyed) return false
    const href = String(win.location.href)
    lastHref = href
    if (!isPatentRoute(href)) {
      teardown()
      claimsWaitStart = null
      return false
    }
    if (current && current.key !== patentKeyFromUrl(href)) teardown()
    return true
  }

  function tryStart() {
    timer = null
    if (destroyed || !routeCheck() || current) return
    const href = String(win.location.href)
    const ready = checkReady(doc, href)
    let go = ready.ok
    if (!go && ready.onlyClaimsMissing) {
      const now = Date.now()
      if (!claimsWaitStart || claimsWaitStart.key !== ready.key) claimsWaitStart = { key: ready.key, at: now }
      if (now - claimsWaitStart.at >= claimsGraceMs) go = true
      else schedule(claimsGraceMs - (now - claimsWaitStart.at))
    } else if (!ready.onlyClaimsMissing) {
      claimsWaitStart = null
    }
    if (!go) return
    let brief
    try {
      brief = extract(doc, href)
    } catch {
      return
    }
    if (!brief || typeof brief.patent_number !== 'string' || brief.patent_number.trim() === '') return
    const session = {
      id: newSessionId(win),
      key: ready.key,
      patentNumber: brief.patent_number,
      brief,
      destroyFns: [],
      onDestroy(fn) {
        if (typeof fn === 'function') this.destroyFns.push(fn)
      },
    }
    current = session
    claimsWaitStart = null
    safely(onSession, session)
  }

  /** Debounced check with a maximum wait so a page that never goes quiet is still read. */
  function schedule(delay = debounceMs) {
    if (destroyed) return
    const now = Date.now()
    if (timer !== null) {
      if (now - timerStartedAt >= maxWaitMs) return
      win.clearTimeout(timer)
    } else {
      timerStartedAt = now
    }
    timer = win.setTimeout(tryStart, delay)
  }

  const onNavigation = () => {
    if (destroyed) return
    if (routeCheck()) schedule()
  }

  // history wrappers
  const history = win.history
  const wrapped = []
  for (const name of ['pushState', 'replaceState']) {
    const original = history[name]
    const hadOwn = Object.prototype.hasOwnProperty.call(history, name)
    const wrapper = function (...args) {
      const result = original.apply(this, args)
      safely(onNavigation)
      return result
    }
    history[name] = wrapper
    wrapped.push({ name, original, hadOwn, wrapper })
  }

  win.addEventListener('popstate', onNavigation)
  const nav = win.navigation
  nav?.addEventListener?.('currententrychange', onNavigation)

  const observer = new win.MutationObserver(() => {
    if (destroyed) return
    if (String(win.location.href) !== lastHref) routeCheck()
    if (!current && isPatentRoute(win.location.href)) schedule()
  })
  observer.observe(doc.body ?? doc.documentElement, { childList: true, subtree: true, characterData: true })

  onNavigation()

  return {
    destroy() {
      if (destroyed) return
      teardown()
      destroyed = true
      if (timer !== null) win.clearTimeout(timer)
      timer = null
      observer.disconnect()
      win.removeEventListener('popstate', onNavigation)
      nav?.removeEventListener?.('currententrychange', onNavigation)
      for (const { name, original, hadOwn, wrapper } of wrapped) {
        if (history[name] !== wrapper) continue // someone wrapped on top of us: leave theirs, ours is inert
        if (hadOwn) history[name] = original
        else delete history[name]
      }
    },
  }
}

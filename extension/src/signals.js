// Page-view tracker: how a user uses the Google Patents PAGE (not the widget). Per patent page view
// it keeps running totals of
//   - dwell: how long the tab was visible while the session lived,
//   - how long each of seven page sections was visible (an element counts while at least 40% of it
//     intersects the viewport and the tab is visible),
// and reports them as cumulative heartbeats. The backend upserts by session id and ignores lower
// values, so a lost heartbeat is repaired by the next one.
//
// It reads nothing else: no page text, no addresses, no search terms, no interaction events of any
// kind, no storage. The payload holds the per-session id, the patent number and numbers.
import { isUsPatentNumber, normalizePatentNumber } from './claimrefs.js'

export const SECTION_NAMES = ['claims', 'description', 'classifications', 'citations', 'family', 'legal_events', 'landscapes']

// Real Google Patents elements (Task 7, Step 0). A selector may match several elements (the section
// is visible while any of them is). Elements that are not rendered yet are picked up later.
export const SECTION_SELECTORS = {
  claims: 'patent-text[name="claims"]',
  description: 'patent-text[name="description"]',
  classifications: 'classification-viewer',
  citations: 'h3#patentCitations + div.responsive-table, h3#citedBy + div.responsive-table',
  family:
    'application-timeline, h3#parentApplications + div.responsive-table, h3#applicationChildApps + div.responsive-table',
  legal_events: 'h3#legalEvents + div.responsive-table',
  landscapes: 'landscape-link',
}

export const MAX_DURATION_MS = 86_400_000 // the backend's per-value limit
export const VISIBLE_RATIO = 0.4
const RATIO_EPSILON = 1e-4 // an IntersectionObserver ratio of 0.39999999 is the 40% threshold, rounded
export const BOUNCE_DWELL_MS = 10_000
export const VIEWED_MS = 3_000
const MIN_LIFETIME_MS = 1_000 // a session shorter than this sends nothing
const MAX_GAP_MS = 60_000 // one clock step counts at most this long (a suspended machine must not add hours)
const RESCAN_DELAY_MS = 500
const IN_FLIGHT_TIMEOUT_MS = 30_000 // a heartbeat request that has not settled by then is treated as failed
const THRESHOLDS = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]
// A section taller than 2.5 viewports can never reach 40% of its own height in view, so for those
// the middle 40% band of the viewport stands in: it counts while the element overlaps that band.
const BAND_MARGIN = '-30% 0px -30% 0px'

const INERT = Object.freeze({ destroy() {}, snapshot: () => null })

const clampMs = (value) => Math.min(MAX_DURATION_MS, Math.max(0, Math.round(Number.isFinite(value) ? value : 0)))

/**
 * @param {Document} doc
 * @param {object} options
 * @param {{id: string, patentNumber: string}} options.session
 * @param {(payload: object) => unknown} options.sendHeartbeat  may return a promise; failures are ignored
 * @param {Record<string, string>} [options.sectionSelectors]
 * @param {() => number} [options.now]                          milliseconds, may be fractional
 * @param {number} [options.intervalMs]
 * @param {typeof IntersectionObserver} [options.IntersectionObserverImpl]
 * @param {typeof MutationObserver} [options.MutationObserverImpl]
 * @param {EventTarget} [options.visibilityTarget]               receives "visibilitychange" (default: doc)
 * @returns {{destroy: () => void, snapshot: () => object | null}}
 */
export function createTracker(
  doc,
  {
    session,
    sendHeartbeat,
    sectionSelectors = SECTION_SELECTORS,
    now = () => Date.now(),
    intervalMs = 15000,
    IntersectionObserverImpl,
    MutationObserverImpl,
    visibilityTarget,
  } = {},
) {
  const patentNumber = normalizePatentNumber(session?.patentNumber ?? '')
  if (!session || typeof session.id !== 'string' || !session.id || !isUsPatentNumber(patentNumber)) return INERT
  if (typeof sendHeartbeat !== 'function') return INERT

  const win = doc.defaultView
  const IO = IntersectionObserverImpl ?? win?.IntersectionObserver
  const MO = MutationObserverImpl ?? win?.MutationObserver
  const names = SECTION_NAMES.filter((name) => typeof sectionSelectors[name] === 'string')
  const visTarget = visibilityTarget ?? doc

  let stopped = false
  let tabVisible = doc.visibilityState !== 'hidden'
  const startedAt = now()
  let last = startedAt
  let dwell = 0
  const sectionMs = Object.fromEntries(SECTION_NAMES.map((name) => [name, 0]))
  const tracked = new Map() // element -> {name, ratio, band, height}

  let sentSig = null // the signature of the last payload delivered (initially the all-zero one: nothing to report yet)
  let inFlight = false
  let dirty = false
  let intervalId = null
  let rescanTimer = null
  let inFlightTimer = null

  // ---- time accounting -------------------------------------------------

  function sectionVisible(name) {
    const viewport = win?.innerHeight ?? 0
    for (const state of tracked.values()) {
      if (state.name !== name) continue
      if (state.ratio >= VISIBLE_RATIO - RATIO_EPSILON) return true
      if (state.band && state.height * VISIBLE_RATIO > viewport) return true
    }
    return false
  }

  /** Add the time since the last call to the totals, using the state that held during that time. */
  function accrue() {
    const t = now()
    const dt = Math.min(MAX_GAP_MS, Math.max(0, t - last))
    last = t // also after a backwards step (rebase), so accrual carries on from the new reading
    if (!tabVisible || dt === 0) return
    dwell += dt
    for (const name of names) if (sectionVisible(name)) sectionMs[name] += dt
  }

  function buildPayload() {
    const dwellMs = clampMs(dwell)
    const sections = {}
    for (const name of SECTION_NAMES) sections[name] = clampMs(sectionMs[name])
    const viewed = SECTION_NAMES.some((name) => sections[name] >= VIEWED_MS)
    return {
      session_id: session.id,
      patent_number: patentNumber,
      dwell_ms: dwellMs,
      sections,
      bounce: dwellMs < BOUNCE_DWELL_MS && !viewed,
    }
  }

  // ---- sending ---------------------------------------------------------

  function push() {
    if (!stopped) accrue()
    const payload = buildPayload()
    const sig = JSON.stringify(payload)
    if (sig === sentSig) return
    if (inFlight) {
      dirty = true
      return
    }
    inFlight = true
    const previous = sentSig
    sentSig = sig
    let settled = false
    const settle = (failed) => {
      if (settled) return // the timeout and the request itself can both report; the first one wins
      settled = true
      if (inFlightTimer !== null) clearTimeout(inFlightTimer)
      inFlightTimer = null
      if (failed && sentSig === sig) sentSig = previous // not delivered: the next heartbeat may carry it again
      inFlight = false
      if (dirty) {
        dirty = false
        push() // once more, with the totals as they are now
      }
    }
    inFlightTimer = setTimeout(() => settle(true), IN_FLIGHT_TIMEOUT_MS)
    let result
    try {
      result = sendHeartbeat(payload)
    } catch {
      settle(true)
      return
    }
    Promise.resolve(result).then(
      (res) => settle(res !== null && typeof res === 'object' && res.ok === false),
      () => settle(true),
    )
  }

  /** Send the totals if the session is old enough and they changed since the last send. */
  function flush() {
    if (stopped) return
    if (now() - startedAt < MIN_LIFETIME_MS) return
    push()
  }

  // ---- section elements ------------------------------------------------

  const entryHandler = (kind) => (entries) => {
    if (stopped) return
    accrue()
    for (const entry of entries) {
      const state = tracked.get(entry.target)
      if (!state) continue
      if (kind === 'main') state.ratio = entry.isIntersecting ? Number(entry.intersectionRatio) || 0 : 0
      else {
        state.band = Boolean(entry.isIntersecting)
        const height = entry.boundingClientRect?.height
        if (Number.isFinite(height)) state.height = height
      }
    }
  }

  let mainObserver = null
  let bandObserver = null
  if (typeof IO === 'function') {
    try {
      mainObserver = new IO(entryHandler('main'), { threshold: THRESHOLDS })
      bandObserver = new IO(entryHandler('band'), { rootMargin: BAND_MARGIN, threshold: 0 })
    } catch {
      mainObserver = null
      bandObserver = null
    }
  }

  function scan() {
    rescanTimer = null
    if (stopped || !mainObserver) return
    accrue()
    for (const el of [...tracked.keys()]) {
      if (!doc.contains(el)) {
        tracked.delete(el)
        mainObserver.unobserve(el)
        bandObserver.unobserve(el)
      }
    }
    for (const name of names) {
      let found = []
      try {
        found = [...doc.querySelectorAll(sectionSelectors[name])]
      } catch {
        // an invalid selector must not break the page
      }
      for (const el of found) {
        if (tracked.has(el)) continue
        tracked.set(el, { name, ratio: 0, band: false, height: 0 })
        mainObserver.observe(el)
        bandObserver.observe(el)
      }
    }
  }

  function scheduleScan() {
    if (stopped || rescanTimer !== null) return // throttled: at most one scan per delay, so a busy page cannot starve it
    rescanTimer = setTimeout(scan, RESCAN_DELAY_MS)
  }

  let mutationObserver = null
  if (mainObserver && typeof MO === 'function') {
    try {
      mutationObserver = new MO(scheduleScan)
      mutationObserver.observe(doc.body ?? doc.documentElement, { childList: true, subtree: true })
    } catch {
      mutationObserver = null
    }
  }

  // ---- lifecycle events ------------------------------------------------

  const onVisibility = () => {
    if (stopped) return
    accrue() // time up to now belongs to the old state
    tabVisible = doc.visibilityState !== 'hidden'
    if (!tabVisible) flush()
  }
  const onPageHide = () => flush()
  visTarget.addEventListener('visibilitychange', onVisibility)
  win?.addEventListener('pagehide', onPageHide)

  sentSig = JSON.stringify(buildPayload())
  intervalId = setInterval(flush, intervalMs)
  scan()

  return {
    /** The running totals as a payload (for tests and diagnostics; sends nothing). */
    snapshot() {
      if (!stopped) accrue()
      return buildPayload()
    },
    destroy() {
      if (stopped) return
      flush() // the final totals (when the session lived long enough and they changed)
      stopped = true
      if (intervalId !== null) clearInterval(intervalId)
      intervalId = null
      if (rescanTimer !== null) clearTimeout(rescanTimer)
      rescanTimer = null
      if (inFlightTimer !== null) clearTimeout(inFlightTimer)
      inFlightTimer = null
      mutationObserver?.disconnect()
      mainObserver?.disconnect()
      bandObserver?.disconnect()
      tracked.clear()
      visTarget.removeEventListener('visibilitychange', onVisibility)
      win?.removeEventListener('pagehide', onPageHide)
    },
  }
}

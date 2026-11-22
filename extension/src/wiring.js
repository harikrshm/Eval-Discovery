// Wires the session controller to the widget. Exported separately from content.js (which only
// starts it) so it can be tested with a fake api.
import { api as defaultApi } from './api.js'
import { extractBrief } from './extractor.js'
import { createSessionController } from './session.js'
import { createTracker } from './signals.js'
import { createWidget } from './widget/Widget.js'

/**
 * @param {{win?: Window, api?: object, extract?: Function, onSession?: (session: object) => void,
 *   trackerOptions?: object}} options
 *   `onSession(session)` is an extra hook for tests; `trackerOptions` overrides the tracker's
 *   injectable parts (clock, observer, interval) in tests.
 */
export function startContent({ win = window, api = defaultApi, extract = extractBrief, onSession, trackerOptions } = {}) {
  let widget = null
  const controller = createSessionController({
    win,
    extract,
    onSession(session) {
      widget = createWidget({ doc: win.document, session, api })
      // The page-view tracker lives exactly as long as the session: it starts here (a patent route,
      // page ready) and is destroyed, with a final flush, when the page view ends. It does not start
      // for a non-US patent. Heartbeat failures and 429 are ignored; the next heartbeat repairs them.
      try {
        const tracker = createTracker(win.document, {
          session,
          sendHeartbeat: (payload) => api.pageView?.(payload),
          ...trackerOptions,
        })
        session.onDestroy(() => tracker.destroy())
      } catch {
        // a tracker problem must never affect the page or the widget
      }
      onSession?.(session)
    },
    onTeardown() {
      widget?.destroy()
      widget = null
    },
  })
  return {
    /** The current widget, for tests (its shadow root is closed to the page). */
    get widget() {
      return widget
    },
    destroy() {
      controller.destroy()
      widget?.destroy()
      widget = null
    },
  }
}

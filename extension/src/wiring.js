// Wires the session controller to the widget. Exported separately from content.js (which only
// starts it) so it can be tested with a fake api.
import { api as defaultApi } from './api.js'
import { extractBrief } from './extractor.js'
import { createSessionController } from './session.js'
import { createWidget } from './widget/Widget.js'

/**
 * @param {{win?: Window, api?: object, extract?: Function, onSession?: (session: object) => void}} options
 *   `onSession(session)` is the hook for the tracker (Task 9): session.id, session.patentNumber and
 *   session.onDestroy(fn), whose fn runs when the page view ends.
 */
export function startContent({ win = window, api = defaultApi, extract = extractBrief, onSession } = {}) {
  let widget = null
  const controller = createSessionController({
    win,
    extract,
    onSession(session) {
      widget = createWidget({ doc: win.document, session, api })
      // === Task 9 hook: the tracker starts here. It gets the session id, the patent number and
      // === session.onDestroy(fn) to stop its heartbeats and timers when the page view ends.
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

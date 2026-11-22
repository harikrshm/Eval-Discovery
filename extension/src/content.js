// Content script entry for https://patents.google.com/*. All behaviour lives in wiring.js; it acts
// only on /patent/ routes and never calls the backend itself (the service worker does).
import { startContent } from './wiring.js'

if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) {
  startContent({ win: window })
}

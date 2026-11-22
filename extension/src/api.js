// The content script's only way to reach the backend: a message to the service worker, which
// holds the assist key. Every call resolves (never rejects) with the parsed reply or a fixed error.
import { MESSAGES, failure } from './messages.js'

const defaultRuntime = () => (typeof chrome === 'undefined' ? undefined : chrome.runtime)

export function createApi(getRuntime = defaultRuntime) {
  const call = (type, payload) =>
    new Promise((resolve) => {
      try {
        const runtime = getRuntime()
        runtime.sendMessage({ type, payload }, (reply) => {
          if (runtime.lastError || reply === undefined || reply === null) resolve(failure(0, MESSAGES.generic))
          else resolve(reply)
        })
      } catch {
        resolve(failure(0, MESSAGES.generic)) // e.g. "Extension context invalidated" after a reload
      }
    })
  return {
    chat: (payload) => call('chat', payload),
    rating: (payload) => call('rating', payload),
    reask: (payload) => call('reask', payload),
    pageView: (payload) => call('page_view', payload), // for the tracker (Task 9)
  }
}

export const api = createApi()

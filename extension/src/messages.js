// Fixed texts shared by the service worker and the widget. Errors shown to the user are always one
// of these: never provider text, never anything from the backend, never the assist key.

export const MESSAGES = {
  generic: 'The assistant could not answer right now. Please try again.',
  rateLimited: 'Too many requests. Please wait a moment and try again.',
  notConfigured: 'Assist is not configured.',
  badRequest: 'The request could not be sent.',
}

export function isFixedMessage(text) {
  return Object.values(MESSAGES).includes(text)
}

/** The error object the service worker returns for any failure. */
export function failure(status, message) {
  return { ok: false, status, message }
}

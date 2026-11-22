// Build-time validation of VITE_BACKEND_ORIGIN, the backend host permission written into
// manifest.json. Used by vite.config.js (not part of the extension bundles).

export const DEFAULT_BACKEND_ORIGIN = 'http://127.0.0.1:8000'

/**
 * "https://assist.example.com/" -> "https://assist.example.com" (the validated origin the service
 * worker calls). Unset or blank falls back to the default. Anything that is not a plain http(s)
 * origin with a concrete host throws, so a bad value fails the build instead of widening the permission.
 */
export function backendOrigin(raw) {
  const text = raw === undefined || raw === null ? '' : String(raw).trim()
  if (!text) return DEFAULT_BACKEND_ORIGIN
  if (text.includes('*')) throw new Error(`VITE_BACKEND_ORIGIN must not contain wildcards: ${text}`)
  let url
  try {
    url = new URL(text)
  } catch {
    throw new Error(`VITE_BACKEND_ORIGIN is not a valid URL: ${text}`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`VITE_BACKEND_ORIGIN must be an http: or https: origin, got ${url.protocol}`)
  }
  if (!url.hostname) throw new Error(`VITE_BACKEND_ORIGIN has no host: ${text}`)
  if (url.username || url.password) throw new Error('VITE_BACKEND_ORIGIN must not contain credentials')
  if (url.pathname !== '/' || url.search || url.hash) {
    throw new Error(`VITE_BACKEND_ORIGIN must be an origin only (scheme, host, optional port), got ${text}`)
  }
  return url.origin
}

/** "https://assist.example.com" -> "https://assist.example.com/*" (a host permission pattern). */
export function backendHostPermission(raw) {
  return `${backendOrigin(raw)}/*`
}

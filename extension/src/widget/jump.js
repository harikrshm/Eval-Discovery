// Find a cited claim, paragraph or section on the live Google Patents page, scroll to it
// and highlight it briefly. The page is never left changed: the highlight is inline style
// only, and the element's previous style is put back exactly.

import { SECTION_SELECTORS } from '../signals.js'

const HIGHLIGHT_CSS = 'outline: 3px solid #f5b400; outline-offset: 2px; background-color: #fff3b0'
const DEFAULT_HIGHLIGHT_MS = 2000
const DIGITS = /^[0-9]{1,9}$/

/** The element a reference points at, or null. Model text is only ever compared, never used as a selector. */
export function findTarget(doc, ref) {
  try {
    if (!doc || !ref || typeof ref.target !== 'string') return null
    if (ref.kind === 'claim') {
      if (!DIGITS.test(ref.target)) return null
      const wanted = parseInt(ref.target, 10)
      for (const el of doc.querySelectorAll('.claim')) {
        if (el.querySelector('.claim')) continue // Google wraps each claim in another .claim
        const attr = el.getAttribute('num')
        if (attr !== null && DIGITS.test(attr.trim()) && parseInt(attr, 10) === wanted) return el
      }
      return null
    }
    if (ref.kind === 'paragraph') {
      for (const el of doc.querySelectorAll('.description-paragraph[num]')) {
        if (el.getAttribute('num') === ref.target) return el
      }
      return null
    }
    if (ref.kind === 'section') {
      if (!Object.hasOwn(SECTION_SELECTORS, ref.target)) return null
      return doc.querySelector(SECTION_SELECTORS[ref.target])
    }
  } catch {
    // fall through
  }
  return null
}

export function createJumper(
  doc,
  { win = doc?.defaultView, highlightMs = DEFAULT_HIGHLIGHT_MS, setTimeoutFn, clearTimeoutFn } = {},
) {
  const setTimer = setTimeoutFn ?? ((fn, ms) => setTimeout(fn, ms))
  const clearTimer = clearTimeoutFn ?? ((id) => clearTimeout(id))
  let active = null // { el, cssText, hadAttr, timer }

  function restore() {
    if (!active) return
    const { el, cssText, hadAttr, timer } = active
    active = null
    try {
      clearTimer(timer)
    } catch {
      // ignore
    }
    try {
      if (hadAttr) el.style.cssText = cssText
      else el.removeAttribute('style')
    } catch {
      // ignore
    }
  }

  function prefersReducedMotion() {
    try {
      return Boolean(win?.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches)
    } catch {
      return false
    }
  }

  return {
    canJump(ref) {
      return findTarget(doc, ref) !== null
    },
    jump(ref) {
      const el = findTarget(doc, ref)
      if (!el) return false
      try {
        restore()
        try {
          el.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
        } catch {
          // a page without scrollIntoView still gets the highlight
        }
        const saved = { el, cssText: el.style.cssText, hadAttr: el.hasAttribute('style'), timer: null }
        el.style.cssText = saved.cssText ? `${saved.cssText}${saved.cssText.trimEnd().endsWith(';') ? ' ' : '; '}${HIGHLIGHT_CSS}` : HIGHLIGHT_CSS
        active = saved
        saved.timer = setTimer(() => {
          if (active === saved) restore()
        }, highlightMs)
        return true
      } catch {
        return false
      }
    },
    clear: restore,
  }
}

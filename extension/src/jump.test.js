import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createJumper, findTarget } from './widget/jump.js'
import { load } from './testutil.js'

const claimRef = (target) => ({ start: 0, end: 1, kind: 'claim', target, label: `claim ${target}` })
const paraRef = (target) => ({ start: 0, end: 1, kind: 'paragraph', target, label: `[${target}]` })
const sectionRef = (target) => ({ start: 0, end: 1, kind: 'section', target, label: target })

function fakeWin(reduced) {
  return { matchMedia: vi.fn(() => ({ matches: reduced })) }
}

describe('findTarget on a saved fixture', () => {
  const { doc } = load('us12401720b1.html')
  it('finds a leaf claim, not the outer wrapper', () => {
    const el = findTarget(doc, claimRef('1'))
    expect(el).not.toBeNull()
    expect(el.getAttribute('num')).toBe('00001')
    expect(el.querySelector('.claim')).toBeNull()
    expect(findTarget(doc, claimRef('00001'))).toBe(el)
  })
  it('finds a paragraph by its exact marker digits', () => {
    const el = findTarget(doc, paraRef('0002'))
    expect(el.getAttribute('num')).toBe('0002')
    expect(el.classList.contains('description-paragraph')).toBe(true)
    expect(findTarget(doc, paraRef('2'))).toBeNull()
  })
  it('finds a section through SECTION_SELECTORS', () => {
    expect(findTarget(doc, sectionRef('claims')).getAttribute('name')).toBe('claims')
    expect(findTarget(doc, sectionRef('description')).getAttribute('name')).toBe('description')
  })
  it('returns null for missing or unknown targets', () => {
    expect(findTarget(doc, claimRef('9999'))).toBeNull()
    expect(findTarget(doc, paraRef('9999'))).toBeNull()
    expect(findTarget(doc, sectionRef('nonsense'))).toBeNull()
    expect(findTarget(doc, sectionRef('__proto__'))).toBeNull()
    expect(findTarget(doc, { kind: 'bogus', target: 'x' })).toBeNull()
    expect(findTarget(doc, null)).toBeNull()
    expect(findTarget(doc, paraRef('"]x'))).toBeNull()
  })
})

describe('jumper', () => {
  let doc
  let scroll
  beforeEach(() => {
    vi.useFakeTimers()
    doc = new DOMParser().parseFromString(
      `<body>
        <div class="claim"><div class="claim" num="00001">one</div><div class="claim" num="00002" style="color: red;">two</div></div>
        <div class="description-paragraph" num="0042">para</div>
        <patent-text name="claims"></patent-text>
      </body>`,
      'text/html',
    )
    scroll = vi.fn()
    window.HTMLElement.prototype.scrollIntoView = scroll
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const make = (reduced = false, extra = {}) => createJumper(doc, { win: fakeWin(reduced), ...extra })
  const el1 = () => doc.querySelector('[num="00001"]')
  const el2 = () => doc.querySelector('[num="00002"]')

  it('canJump reflects whether the target exists', () => {
    const j = make()
    expect(j.canJump(claimRef('1'))).toBe(true)
    expect(j.canJump(claimRef('7'))).toBe(false)
    expect(j.canJump(paraRef('0042'))).toBe(true)
    expect(j.canJump(sectionRef('claims'))).toBe(true)
    expect(j.canJump(sectionRef('family'))).toBe(false)
  })

  it('scrolls smoothly to the centre by default', () => {
    expect(make(false).jump(claimRef('1'))).toBe(true)
    expect(scroll).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
  })
  it('scrolls without animation when reduced motion is requested', () => {
    expect(make(true).jump(claimRef('1'))).toBe(true)
    expect(scroll).toHaveBeenCalledWith({ block: 'center', behavior: 'auto' })
  })
  it('tolerates a missing matchMedia', () => {
    const j = createJumper(doc, { win: {} })
    expect(j.jump(claimRef('1'))).toBe(true)
    expect(scroll).toHaveBeenCalledWith({ block: 'center', behavior: 'smooth' })
  })

  it('returns false and does nothing for a missing target', () => {
    const j = make()
    expect(j.jump(claimRef('99'))).toBe(false)
    expect(scroll).not.toHaveBeenCalled()
  })

  it('highlights inline and restores the exact previous style after the time', () => {
    const j = make(false, { highlightMs: 500 })
    const before = el2().style.cssText
    expect(before).not.toBe('')
    j.jump(claimRef('2'))
    expect(el2().style.cssText).toContain('outline: 3px solid #f5b400')
    expect(el2().style.cssText).toContain('outline-offset: 2px')
    expect(el2().style.cssText).toContain('background-color: rgb(255, 243, 176)')
    vi.advanceTimersByTime(499)
    expect(el2().style.cssText).not.toBe(before)
    vi.advanceTimersByTime(1)
    expect(el2().style.cssText).toBe(before)
  })

  it('restores an element that had no inline style to having none', () => {
    const j = make()
    j.jump(claimRef('1'))
    expect(el1().style.cssText).not.toBe('')
    vi.advanceTimersByTime(2000)
    expect(el1().style.cssText).toBe('')
  })

  it('keeps only the latest highlight: a second jump restores the first', () => {
    const j = make()
    const before2 = el2().style.cssText
    j.jump(claimRef('1'))
    j.jump(claimRef('2'))
    expect(el1().style.cssText).toBe('')
    expect(el2().style.cssText).toContain('rgb(255, 243, 176)')
    vi.advanceTimersByTime(2000)
    expect(el2().style.cssText).toBe(before2)
  })

  it('re-jumping the same element does not lose its original style', () => {
    const j = make()
    const before2 = el2().style.cssText
    j.jump(claimRef('2'))
    vi.advanceTimersByTime(1000)
    j.jump(claimRef('2'))
    vi.advanceTimersByTime(1999)
    expect(el2().style.cssText).toContain('rgb(255, 243, 176)')
    vi.advanceTimersByTime(1)
    expect(el2().style.cssText).toBe(before2)
  })

  it('a failed jump leaves the current highlight alone', () => {
    const j = make()
    j.jump(claimRef('1'))
    expect(j.jump(claimRef('99'))).toBe(false)
    expect(el1().style.cssText).toContain('rgb(255, 243, 176)')
  })

  it('clear() restores immediately and cancels the timer', () => {
    const clearTimeoutFn = vi.fn((id) => clearTimeout(id))
    const j = make(false, { clearTimeoutFn })
    j.jump(claimRef('1'))
    j.clear()
    expect(el1().style.cssText).toBe('')
    expect(clearTimeoutFn).toHaveBeenCalled()
    expect(() => j.clear()).not.toThrow()
    el1().style.cssText = 'color: blue;'
    vi.advanceTimersByTime(5000)
    expect(el1().style.cssText).toBe('color: blue;')
  })

  it('does not throw when the element was removed before the timer fires', () => {
    const j = make()
    const el = el1()
    j.jump(claimRef('1'))
    el.remove()
    expect(() => vi.advanceTimersByTime(2000)).not.toThrow()
    expect(el.style.cssText).toBe('')
    expect(() => j.clear()).not.toThrow()
  })

  it('does not throw when scrollIntoView throws', () => {
    window.HTMLElement.prototype.scrollIntoView = () => {
      throw new Error('boom')
    }
    expect(() => make().jump(claimRef('1'))).not.toThrow()
  })

  it('uses injected timer functions', () => {
    const setTimeoutFn = vi.fn(() => 7)
    const clearTimeoutFn = vi.fn()
    const j = make(false, { setTimeoutFn, clearTimeoutFn, highlightMs: 123 })
    j.jump(claimRef('1'))
    expect(setTimeoutFn).toHaveBeenCalledWith(expect.any(Function), 123)
    j.clear()
    expect(clearTimeoutFn).toHaveBeenCalledWith(7)
  })

  it('jumps to a paragraph and a section', () => {
    const j = make()
    expect(j.jump(paraRef('0042'))).toBe(true)
    expect(j.jump(sectionRef('claims'))).toBe(true)
    expect(doc.querySelector('[num="0042"]').style.cssText).toBe('')
  })
})

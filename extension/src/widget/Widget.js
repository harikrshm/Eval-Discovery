// The Patent Assist overlay: a Shadow DOM widget (closed mode, styles inside the shadow root) with a
// launcher button, a chat panel, first-turn quick-question chips, one suggested follow-up and a single
// feedback request after the third answer.
// Everything shown is built from DOM nodes and textContent, never innerHTML.
import { isGrantedPatentNumber, isUsPatentNumber } from '../claimrefs.js'
import { MESSAGES, isFixedMessage } from '../messages.js'
import { normalizeReferences, renderAnswer as renderAnswerBody } from './answer-view.js'
import { CHIPS } from './chips.js'
import { FEEDBACK_AFTER_QUESTIONS, pickFollowUp } from './followups.js'
import { createJumper } from './jump.js'
import STYLES from './styles.css?raw'

export { displayText } from './answer-view.js'

export const NON_US_NOTICE = 'Patent Assist works on US utility patents and applications only.'
export const INCOMPLETE_NOTE = 'Some page sections could not be read.'
export const FOOTER_TEXT = 'Not legal advice. Answers are based on the text of this page.'
export const THINKING_TEXT = 'Thinking…'

// The backend's caps (backend/api/schemas.py, backend/api/limits.py).
export const LIMITS = {
  question: 2000,
  selection: 800,
  reason: 500,
  historyTurns: 20,
  conversationTurns: 42,
  userTurn: 2000,
  assistantTurn: 16000,
  requestChars: 120000,
  reaskWindowMs: 90000,
}
const RETRY_DELAY_MS = 3000
const PILL_MISSING_TITLE = "Couldn't find this on the page"

// ---- pure helpers --------------------------------------------------------

export function cleanSelection(text) {
  return String(text ?? '').replace(/\s+/g, ' ').trim().slice(0, LIMITS.selection)
}

/** One plain string: the quoted selection (if any) followed by what the user typed, capped at 2000. */
export function buildQuestion(typed, selection) {
  const t = String(typed ?? '').trim()
  const s = cleanSelection(selection)
  if (!s) return t.slice(0, LIMITS.question)
  const quoted = `Explain this passage: "${s}"`
  return (t ? `${quoted} ${t}` : quoted).slice(0, LIMITS.question)
}

/** A re-ask: the next answer has the same intent as the previous one and arrives within 90 s of it. */
export function shouldReask(previous, intent, answeredAt) {
  return Boolean(previous) && previous.intent === intent && answeredAt - previous.answeredAt <= LIMITS.reaskWindowMs
}

const capTurn = (t) => ({ role: t.role, content: t.content.slice(0, t.role === 'user' ? LIMITS.userTurn : LIMITS.assistantTurn) })

/**
 * The last turns that fit the backend limits: at most `maxTurns`, each capped by role, total size
 * under the request cap (minus `reserve` for the question), starting with a user turn.
 */
export function fitConversation(turns, { maxTurns = LIMITS.conversationTurns, reserve = 0 } = {}) {
  const out = turns.slice(-maxTurns).map(capTurn)
  const budget = LIMITS.requestChars - reserve
  const size = () => out.reduce((n, t) => n + t.content.length, 0)
  while (out.length && (out[0].role !== 'user' || size() > budget)) out.shift()
  return out
}

// ---- DOM helpers ---------------------------------------------------------

function el(doc, tag, attrs = {}, text) {
  const node = doc.createElement(tag)
  for (const [name, value] of Object.entries(attrs)) {
    if (value === true) node.setAttribute(name, '')
    else if (value !== false && value !== undefined && value !== null) node.setAttribute(name, String(value))
  }
  if (text !== undefined) node.textContent = text
  return node
}

function defaultGetSelection(doc, host) {
  const sel = doc.defaultView?.getSelection?.()
  if (!sel || sel.isCollapsed) return ''
  const anchor = sel.anchorNode
  // Never quote the widget's own text back at the assistant.
  if (anchor && (anchor === host || host.contains(anchor) || anchor.getRootNode?.() !== doc)) return ''
  return sel.toString()
}

const isFailure = (res) => !res || typeof res !== 'object' || res.ok === false

/**
 * @param {{doc: Document, session: {id: string, brief: object}, api: {chat: Function, rating: Function, reask: Function},
 *   getSelection?: () => string, now?: () => number, jumper?: ReturnType<typeof createJumper>}} options
 */
export function createWidget({ doc, session, api, getSelection, now = () => Date.now(), jumper = createJumper(doc) }) {
  for (const stale of doc.querySelectorAll('[data-patent-assist]')) stale.remove()
  const host = el(doc, 'div', { 'data-patent-assist': true })
  const shadow = host.attachShadow({ mode: 'closed' })
  const readSelection = getSelection ?? (() => defaultGetSelection(doc, host))
  const brief = session.brief
  const usPatent = isUsPatentNumber(brief.patent_number)

  /** Per-session state. `turns` is what is sent back as history: only answered questions. */
  const state = {
    turns: [],
    questions: [],
    asked: [], // the text of every question sent in this page view (a retry is not a new question)
    feedback: 'idle', // idle -> shown -> done: the feedback request appears at most once
    busy: false,
    open: false,
    remembered: '',
    destroyed: false,
  }
  const claimNumbers = (brief.claims ?? [])
    .map((c) => c?.number)
    .filter(Number.isInteger)
    .sort((a, b) => a - b)
  const timers = new Set()

  // ---- structure ----
  const style = el(doc, 'style')
  style.textContent = STYLES
  const root = el(doc, 'div', { class: 'root' })
  const launcher = el(doc, 'button', { class: 'launcher', type: 'button', 'aria-label': 'Open Patent Assist', 'aria-expanded': 'false', 'aria-controls': 'pa-panel' }, 'Patent Assist')
  const panel = el(doc, 'section', { class: 'panel', id: 'pa-panel', role: 'dialog', 'aria-label': 'Patent Assist', hidden: true })
  const header = el(doc, 'header')
  const closeBtn = el(doc, 'button', { class: 'close', type: 'button', 'aria-label': 'Close Patent Assist' }, '×')
  header.append(el(doc, 'h2', {}, 'Patent Assist'), el(doc, 'span', { class: 'pn' }, brief.patent_number), closeBtn)
  const incomplete = Object.values(brief.completeness ?? {}).some((v) => v === false)
  const notice = el(doc, 'p', { class: 'notice', role: 'status', hidden: usPatent }, NON_US_NOTICE)
  const note = el(doc, 'p', { class: 'note', hidden: !incomplete }, INCOMPLETE_NOTE)
  const log = el(doc, 'div', { class: 'messages', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation' })
  const chipRow = el(doc, 'div', { class: 'chips', role: 'group', 'aria-label': 'Quick questions' })
  const chipButtons = CHIPS.map((chip) => {
    const b = el(doc, 'button', { class: 'chip', type: 'button', 'data-chip': chip.id }, chip.label)
    b.addEventListener('click', () => send(chip.question))
    return b
  })
  chipRow.append(...chipButtons)
  const followups = el(doc, 'div', { class: 'followups' })
  const selectionRow = el(doc, 'div', { class: 'selection', hidden: true })
  const selectionText = el(doc, 'span')
  const clearSelection = el(doc, 'button', { type: 'button', 'aria-label': 'Clear selected text' }, 'Clear')
  selectionRow.append(selectionText, clearSelection)
  const composer = el(doc, 'div', { class: 'composer' })
  const input = el(doc, 'textarea', { rows: 2, maxlength: LIMITS.question, 'aria-label': 'Your question', placeholder: 'Ask about this patent' })
  const sendBtn = el(doc, 'button', { class: 'send', type: 'button', 'data-action': 'send' }, 'Send')
  composer.append(input, sendBtn)
  const footer = el(doc, 'p', { class: 'footer' }, FOOTER_TEXT)
  panel.append(header, notice, note, log, chipRow, followups, selectionRow, composer, footer)
  root.append(launcher, panel)
  shadow.append(style, root)
  ;(doc.body ?? doc.documentElement).appendChild(host)

  // ---- small UI helpers ----
  function scrollDown() {
    log.scrollTop = log.scrollHeight
  }
  function setBusy(busy) {
    state.busy = busy
    const off = busy || !usPatent
    input.disabled = off
    sendBtn.disabled = off
    for (const b of chipButtons) b.disabled = off
    for (const b of followups.querySelectorAll('button')) b.disabled = off
  }
  function renderSelection() {
    selectionRow.hidden = state.remembered === ''
    selectionText.textContent = state.remembered === '' ? '' : `Selected on the page: "${state.remembered}"`
  }
  function setOpen(open) {
    if (state.destroyed) return
    state.open = open
    panel.hidden = !open
    launcher.setAttribute('aria-expanded', String(open))
    if (open) (input.disabled ? closeBtn : input).focus()
    else launcher.focus()
  }
  function addMessage(role, text) {
    const node = el(doc, 'div', { class: `msg ${role}` })
    node.append(el(doc, 'p', { class: 'text' }, text))
    log.append(node)
    scrollDown()
    return node
  }
  function removeError() {
    log.querySelector('.error')?.remove()
  }
  function timer(fn, ms) {
    const id = setTimeout(() => {
      timers.delete(id)
      fn()
    }, ms)
    timers.add(id)
  }

  // ---- signals (ratings, re-asks): fire and forget, one silent retry, 429 dropped ----
  function sendSignal(call, payload) {
    const attempt = (isRetry) => {
      const fail = (res) => {
        if (state.destroyed || isRetry || (res && res.status === 429)) return
        timer(() => attempt(true), RETRY_DELAY_MS)
      }
      Promise.resolve()
        .then(() => call(payload))
        .then((res) => (isFailure(res) ? fail(res) : undefined), () => fail(null))
    }
    attempt(false)
  }

  function rate(entry, rating, reason) {
    if (entry.rated || state.destroyed) return
    entry.rated = true
    state.feedback = 'done'
    for (const b of entry.buttons) b.disabled = true
    entry.pressed?.setAttribute('aria-pressed', 'true')
    entry.reasonForm.hidden = true
    entry.thanks.textContent = 'Thanks for the feedback.'
    // `entry` is the answer the card sits under (it owns the lock and the UI); the rating itself is about
    // the whole conversation so far, so it is recorded against the newest answered question.
    const newest = state.questions.at(-1) ?? entry
    const payload = {
      question_id: newest.question_id,
      rating,
      conversation: fitConversation(state.turns),
      brief,
    }
    const text = (reason ?? '').trim().slice(0, LIMITS.reason)
    if (text) payload.reason = text
    sendSignal((p) => api.rating(p), payload)
  }

  /** The one feedback request of a page view, appended under the answer that reached the threshold. */
  function showFeedback(entry, node) {
    state.feedback = 'shown'
    const block = el(doc, 'div', { class: 'feedback' })
    const actions = el(doc, 'div', { class: 'actions' })
    const up = el(doc, 'button', { type: 'button', 'data-action': 'up', 'aria-label': 'Thumbs up: this answer helped' }, 'Helpful')
    const down = el(doc, 'button', { type: 'button', 'data-action': 'down', 'aria-label': 'Thumbs down: this answer did not help' }, 'Not helpful')
    const none = el(doc, 'button', { type: 'button', 'data-action': 'no_answer' }, "Didn't answer my question")
    const dismiss = el(doc, 'button', { type: 'button', 'data-action': 'dismiss' }, 'Dismiss')
    const thanks = el(doc, 'span', { class: 'thanks' })
    const reasonForm = el(doc, 'div', { class: 'reason', hidden: true })
    const reasonInput = el(doc, 'input', { type: 'text', maxlength: LIMITS.reason, 'data-field': 'reason', 'aria-label': 'Reason (optional)', placeholder: 'What was wrong? (optional)' })
    const reasonSend = el(doc, 'button', { type: 'button', 'data-action': 'send-reason' }, 'Send feedback')
    reasonForm.append(reasonInput, reasonSend)
    entry.buttons = [up, down, none, dismiss, reasonSend]
    entry.reasonForm = reasonForm
    entry.thanks = thanks
    up.addEventListener('click', () => {
      entry.pressed = up
      rate(entry, 'up')
    })
    none.addEventListener('click', () => {
      entry.pressed = none
      rate(entry, 'no_answer')
    })
    down.addEventListener('click', () => {
      if (entry.rated) return
      reasonForm.hidden = false
      reasonInput.focus()
    })
    reasonSend.addEventListener('click', () => {
      entry.pressed = down
      rate(entry, 'down', reasonInput.value)
    })
    reasonInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault()
        reasonSend.click()
      }
    })
    dismiss.addEventListener('click', () => {
      if (entry.rated) return
      state.feedback = 'done'
      block.remove()
    })
    actions.append(up, down, none, dismiss, thanks)
    block.append(el(doc, 'p', { class: 'feedback-title' }, 'How is this going?'), actions, reasonForm)
    node.append(block)
    scrollDown()
  }

  /** The assistant's answer: formatted text with reference pills; a pill whose target is not on the page is disabled. */
  function renderAnswer(entry) {
    const node = el(doc, 'div', { class: 'msg assistant' })
    const body = el(doc, 'div', { class: 'text' })
    body.append(
      renderAnswerBody(doc, entry.answer, entry.references, {
        pills: isGrantedPatentNumber(brief.patent_number),
        onJump: (ref) => jumper.jump(ref),
      }),
    )
    for (const pill of body.querySelectorAll('button.pill')) {
      if (!jumper.canJump({ kind: pill.dataset.kind, target: pill.dataset.target })) {
        pill.disabled = true
        pill.title = PILL_MISSING_TITLE
      }
    }
    node.append(body)
    log.append(node)
    scrollDown()
    if (state.feedback === 'idle' && state.questions.length >= FEEDBACK_AFTER_QUESTIONS) showFeedback(entry, node)
  }

  /** One suggested next question, replacing the previous one; none once every candidate has been asked. */
  function renderFollowUp() {
    followups.replaceChildren()
    const entry = state.questions.at(-1)
    if (!entry || !usPatent) return
    const question = pickFollowUp({ intent: entry.intent, references: entry.references, asked: state.asked, claimNumbers })
    if (question === null) return
    const button = el(doc, 'button', { class: 'followup', type: 'button', 'data-action': 'followup', disabled: state.busy }, question)
    button.addEventListener('click', () => send(question, false, 'followup'))
    followups.append(button)
  }

  function showError(res, question, source) {
    const message = res && isFixedMessage(res.message) ? res.message : MESSAGES.generic
    const box = el(doc, 'div', { class: 'error', role: 'alert' })
    box.append(el(doc, 'p', {}, message))
    if (message !== MESSAGES.notConfigured) {
      const retry = el(doc, 'button', { type: 'button', 'data-action': 'retry' }, 'Retry')
      retry.addEventListener('click', () => send(question, true, source))
      box.append(retry)
    }
    log.append(box)
    scrollDown()
  }

  // ---- chat ----
  // `source` is where the question came from: 'user' (typed text or a chip) or 'followup' (the code-picked button).
  async function send(question, isRetry = false, source = 'user') {
    if (state.busy || state.destroyed || !usPatent || !question) return
    removeError()
    if (!isRetry) {
      state.asked.push(question)
      chipRow.hidden = true // the chips are for the first turn only
    }
    setBusy(true)
    if (!isRetry) addMessage('user', question)
    const thinking = el(doc, 'div', { class: 'thinking' }, THINKING_TEXT)
    log.append(thinking)
    scrollDown()

    const payload = {
      session_id: session.id,
      brief,
      history: fitConversation(state.turns, { maxTurns: LIMITS.historyTurns, reserve: question.length }),
      question,
    }
    let res
    try {
      res = await api.chat(payload)
    } catch {
      res = null
    }
    if (state.destroyed) return
    thinking.remove()
    setBusy(false)
    if (isFailure(res) || typeof res.answer !== 'string' || typeof res.question_id !== 'string') {
      showError(res, question, source)
      renderFollowUp() // recomputed: the failed question now counts as asked
    } else {
      const previous = state.questions.at(-1)
      const entry = {
        question_id: res.question_id,
        text: question,
        answer: res.answer, // exactly as returned: this is what is sent back as history
        references: normalizeReferences(res.references, res.answer.length),
        intent: String(res.intent ?? ''),
        source,
        answeredAt: now(),
        rated: false,
        reasked: false,
      }
      state.turns.push({ role: 'user', content: question }, { role: 'assistant', content: res.answer })
      state.questions.push(entry)
      renderAnswer(entry)
      renderFollowUp()
      // A follow-up the code suggested is not the user rephrasing: it never counts as a re-ask.
      if (source !== 'followup' && previous && !previous.reasked && shouldReask(previous, entry.intent, entry.answeredAt)) {
        previous.reasked = true
        sendSignal((p) => api.reask(p), {
          question_id: previous.question_id,
          conversation: fitConversation(state.turns),
          brief,
        })
      }
    }
    if (state.open && !input.disabled) input.focus()
  }

  function submitFromInput() {
    if (state.busy || state.destroyed || !usPatent) return
    const selection = cleanSelection(readSelection()) || state.remembered
    const question = buildQuestion(input.value, selection)
    if (!question) return
    input.value = ''
    state.remembered = ''
    renderSelection()
    send(question)
  }

  // ---- events ----
  launcher.addEventListener('click', () => setOpen(true))
  closeBtn.addEventListener('click', () => setOpen(false))
  sendBtn.addEventListener('click', submitFromInput)
  clearSelection.addEventListener('click', () => {
    state.remembered = ''
    renderSelection()
  })
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault()
      submitFromInput()
    }
  })
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.open) {
      e.stopPropagation()
      setOpen(false)
    }
  })
  // Keep typing inside the widget: the page's own shortcuts (I, W/A/S/D, arrows) listen on the
  // document and would otherwise see these events retargeted to the host. Registered after the
  // Escape handler so that one still runs first.
  for (const type of ['keydown', 'keyup', 'keypress']) {
    root.addEventListener(type, (e) => e.stopPropagation())
  }
  // A click on the input moves the page selection away, so remember the last one made on the page.
  const onSelectionChange = () => {
    if (state.destroyed) return
    const text = cleanSelection(readSelection())
    if (text) {
      state.remembered = text
      renderSelection()
    }
  }
  doc.addEventListener('selectionchange', onSelectionChange)

  setBusy(false)

  return {
    host,
    shadow,
    open: () => setOpen(true),
    close: () => setOpen(false),
    destroy() {
      if (state.destroyed) return
      state.destroyed = true
      for (const id of timers) clearTimeout(id)
      timers.clear()
      doc.removeEventListener('selectionchange', onSelectionChange)
      jumper.clear()
      host.remove()
    },
  }
}

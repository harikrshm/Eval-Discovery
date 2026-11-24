import { describe, expect, it } from 'vitest'
import { FEEDBACK_AFTER_QUESTIONS, pickFollowUp } from './widget/followups.js'

const claimRef = (target) => ({ start: 0, end: 1, kind: 'claim', target: String(target), label: `claim ${target}` })
const pick = (intent, { references = [], asked = [], claimNumbers = [1, 2, 3] } = {}) =>
  pickFollowUp({ intent, references, asked, claimNumbers })

describe('FEEDBACK_AFTER_QUESTIONS', () => {
  it('is 3', () => {
    expect(FEEDBACK_AFTER_QUESTIONS).toBe(3)
  })
})

describe('pickFollowUp: one assertion per table row', () => {
  it('claim_explain with no claim references suggests claim 1', () => {
    expect(pick('claim_explain')).toBe('What does claim 1 add?')
  })
  it('claim_explain suggests the claim after the highest one cited', () => {
    expect(pick('claim_explain', { references: [claimRef(1), claimRef(2)] })).toBe('What does claim 3 add?')
    expect(pick('claim_explain', { references: [claimRef(2), claimRef(1)], claimNumbers: [1, 2, 5] })).toBe('What does claim 5 add?')
  })
  it('ignores non-claim references when finding the highest claim', () => {
    const refs = [{ start: 0, end: 1, kind: 'paragraph', target: '0042', label: '[0042]' }, claimRef(1)]
    expect(pick('claim_explain', { references: refs })).toBe('What does claim 2 add?')
  })
  it('claim_explain falls back when the cited claim is the last one', () => {
    expect(pick('claim_explain', { references: [claimRef(3)] })).toBe('What do these CPC codes mean?')
  })
  it('claim_explain falls through to "Is this still in force?"', () => {
    expect(pick('claim_explain', { references: [claimRef(3)], asked: ['What do these CPC codes mean?'] })).toBe('Is this still in force?')
  })
  it('claim_scope uses the same candidates as claim_explain', () => {
    expect(pick('claim_scope', { references: [claimRef(1)] })).toBe('What does claim 2 add?')
    expect(pick('claim_scope', { references: [claimRef(3)] })).toBe('What do these CPC codes mean?')
  })
  it('classification', () => {
    expect(pick('classification')).toBe('Is this still in force?')
    expect(pick('classification', { asked: ['Is this still in force?'] })).toBe('Who cites this patent?')
  })
  it('status_family', () => {
    expect(pick('status_family')).toBe('What do these CPC codes mean?')
    expect(pick('status_family', { asked: ['What do these CPC codes mean?'] })).toBe('Explain claim 1')
  })
  it('citations_prior_art', () => {
    expect(pick('citations_prior_art')).toBe('Explain claim 1')
    expect(pick('citations_prior_art', { asked: ['Explain claim 1'] })).toBe('Is this still in force?')
  })
  it('legal_opinion_request', () => {
    expect(pick('legal_opinion_request')).toBe('What does claim 1 cover?')
    expect(pick('legal_opinion_request', { asked: ['What does claim 1 cover?'] })).toBe('Is this still in force?')
  })
  it('any other intent', () => {
    for (const intent of ['law_concept', 'navigation_help', 'other', 'something_new', '']) {
      expect(pick(intent)).toBe('Explain claim 1')
      expect(pick(intent, { asked: ['Explain claim 1'] })).toBe('What do these CPC codes mean?')
      expect(pick(intent, { asked: ['Explain claim 1', 'What do these CPC codes mean?'] })).toBe('Is this still in force?')
    }
  })
})

describe('pickFollowUp: skipping', () => {
  it('skips an already-asked candidate, compared trimmed and case-insensitively', () => {
    expect(pick('classification', { asked: ['  is THIS still in force?  '] })).toBe('Who cites this patent?')
  })
  it('skips a claim candidate whose number does not exist', () => {
    expect(pick('legal_opinion_request', { claimNumbers: [2, 3] })).toBe('Is this still in force?')
    expect(pick('status_family', { asked: ['What do these CPC codes mean?'], claimNumbers: [] })).toBeNull()
    expect(pick('claim_explain', { references: [claimRef(1)], claimNumbers: [1] })).toBe('What do these CPC codes mean?')
  })
  it('returns null when every candidate has been asked', () => {
    expect(pick('classification', { asked: ['Is this still in force?', 'Who cites this patent?'] })).toBeNull()
    expect(pick('other', { asked: ['Explain claim 1', 'What do these CPC codes mean?', 'Is this still in force?'] })).toBeNull()
  })
  it('tolerates missing inputs', () => {
    expect(pickFollowUp({ intent: undefined, references: undefined, asked: undefined, claimNumbers: undefined })).toBe('What do these CPC codes mean?')
  })
})

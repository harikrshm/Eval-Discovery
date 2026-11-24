import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  buildClaimTree,
  isGrantedPatentNumber,
  isUsPatentNumber,
  normalizePatentNumber,
  parseClaimRefs,
} from './claimrefs.js'

// The same table the Python suite (tests/test_assist_brief.py) iterates.
const CASES = JSON.parse(
  readFileSync(resolve(import.meta.dirname, '../../tests/fixtures/claim_ref_cases.json'), 'utf8'),
)

describe('shared claim-reference cases', () => {
  it('loads every case', () => {
    expect(CASES.length).toBeGreaterThanOrEqual(24)
  })
  it.each(CASES.map((c, i) => [i, c.number, c.text.slice(0, 40), c.expected]))(
    'case %i (claim %i: %s) -> %j',
    (i, _number, _label, expected) => {
      expect(parseClaimRefs(CASES[i].number, CASES[i].text)).toEqual(expected)
    },
  )
  it('a six-digit number is a number', () => {
    expect(parseClaimRefs(1_000_000, 'of claim 123456')).toEqual([123456])
  })
})

describe('buildClaimTree', () => {
  it('computes depends_on and independent', () => {
    const tree = buildClaimTree([
      { number: 1, text: 'A device.' },
      { number: 2, text: 'The device of claim 1.' },
      { number: 3, text: 'The device of claims 1 or 2, and claim 5.' },
      { number: 4, text: 'A method using the device of claim 9.' },
    ])
    expect(tree.map((c) => [c.number, c.depends_on, c.independent])).toEqual([
      [1, [], true],
      [2, [1], false],
      [3, [1, 2], false],
      [4, [], true],
    ])
  })
})

describe('patent numbers', () => {
  it.each(['US10000000B2', 'US20160266243A1', 'us7629993b2', 'US7629993', 'US5123456A'])('%s is US', (n) => {
    expect(isUsPatentNumber(n)).toBe(true)
  })
  it.each(['EP1234567A1', 'WO2020123456A1', 'CN123456A', '', 'US', 'USABC', '10000000', 'US 10,000,000 B2', 'US10,000,000', 'US\uFF11\uFF12\uFF13\uFF14\uFF15\uFF16', null, undefined])(
    '%s is not US',
    (n) => {
      expect(isUsPatentNumber(n)).toBe(false)
    },
  )
  it('normalises page-derived text', () => {
    expect(normalizePatentNumber('US 10,000,000 B2')).toBe('US10000000B2')
    expect(normalizePatentNumber(' us-6756997-b1 ')).toBe('US6756997B1')
    expect(normalizePatentNumber(undefined)).toBe('')
    expect(isUsPatentNumber(normalizePatentNumber('US 10,000,000 B2'))).toBe(true)
  })
})

describe('isGrantedPatentNumber', () => {
  it.each(['US12401720B1', 'US6756997B1', 'US10000000B2', 'us7629993b2', ' US10000000E ', 'US10000000E1'])(
    '%s is granted',
    (n) => {
      expect(isGrantedPatentNumber(n)).toBe(true)
    },
  )
  it.each([
    'US20250119367A1',
    'US20250119367A9',
    'US7629993',
    'USD123456S',
    'USPP12345P',
    'US10000000B12',
    'EP4517554A1',
    'EP4517554B1',
    '',
    null,
    undefined,
    12,
    {},
  ])('%s is not granted', (n) => {
    expect(isGrantedPatentNumber(n)).toBe(false)
  })
})

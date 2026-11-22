import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const FIXTURES = resolve(import.meta.dirname, '../fixtures')

/** Parse a committed trimmed fixture into a jsdom Document (offline, local file). */
export function load(name) {
  const html = readFileSync(resolve(FIXTURES, name), 'utf8')
  return { doc: new DOMParser().parseFromString(html, 'text/html'), html }
}

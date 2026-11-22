// Run the extractor over each committed trimmed fixture and write
// fixtures/<name>.expected.json (validated by tests/test_assist_fixtures.py with the
// backend Brief model). The files are reviewed by hand against the page, then
// committed; re-run only when the extractor or the fixtures change.
//   node scripts/export-expected.js
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { JSDOM } from 'jsdom'
import { extractBrief } from '../src/extractor.js'

const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'fixtures')
const FIXTURES = {
  'ep4517554a1': 'EP4517554A1',
  'us12401720b1': 'US12401720B1',
  'us6756997b1': 'US6756997B1',
  'us20250119367a1': 'US20250119367A1',
}
for (const [name, number] of Object.entries(FIXTURES)) {
  const doc = new JSDOM(fs.readFileSync(path.join(dir, name + '.html'), 'utf8')).window.document
  const brief = extractBrief(doc, `https://patents.google.com/patent/${number}/en`)
  fs.writeFileSync(path.join(dir, name + '.expected.json'), JSON.stringify(brief, null, 1) + '\n')
  console.log(name, JSON.stringify(brief).length, 'chars')
}

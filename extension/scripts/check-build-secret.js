// Build twice into a scratch directory and check where the assist key ends up.
//   1. With a random test key: it must be in dist-keycheck/background.js and NOT in content.js.
//   2. Without a key: the build must still succeed and background.js must carry the
//      "Assist is not configured" text (the runtime answer when there is no key).
// Run: node scripts/check-build-secret.js  (also run by src/build.test.js)
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const outName = 'dist-keycheck'
const out = path.join(root, outName)
const vite = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js')

function build(key) {
  const env = { ...process.env, ASSIST_BUILD_OUT_DIR: outName }
  delete env.VITE_ASSIST_KEY
  if (key) env.VITE_ASSIST_KEY = key
  for (const mode of ['content', 'background']) {
    execFileSync(process.execPath, [vite, 'build', '--mode', mode], { cwd: root, env, stdio: 'pipe' })
  }
}
const read = (name) => fs.readFileSync(path.join(out, name), 'utf8')
function fail(message) {
  fs.rmSync(out, { recursive: true, force: true })
  console.error(`FAIL: ${message}`)
  process.exit(1)
}

try {
  const key = `test-key-${randomUUID()}`
  build(key)
  const content = read('content.js')
  const background = read('background.js')
  const manifest = read('manifest.json')
  if (content.includes(key)) fail('the assist key is in content.js')
  if (manifest.includes(key)) fail('the assist key is in manifest.json')
  if (!background.includes(key)) fail('the assist key is missing from background.js')
  console.log('OK: key absent from content.js')
  console.log('OK: key present in background.js')

  build('')
  const unconfigured = read('background.js')
  if (!unconfigured.includes('Assist is not configured')) fail('background.js lacks the "not configured" text')
  if (unconfigured.includes(key)) fail('the old key is in the unconfigured build')
  console.log('OK: unconfigured build succeeds')
} catch (error) {
  fail(String(error?.stderr || error?.message || error))
} finally {
  fs.rmSync(out, { recursive: true, force: true })
}

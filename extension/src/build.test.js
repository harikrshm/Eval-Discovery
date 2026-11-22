import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Builds into a scratch directory (dist-keycheck) with a random test key and greps the bundles.
describe('build output', () => {
  it('inlines the assist key into background.js only, and builds without a key', () => {
    const out = execFileSync(process.execPath, [resolve(import.meta.dirname, '../scripts/check-build-secret.js')], {
      cwd: resolve(import.meta.dirname, '..'),
      encoding: 'utf8',
      timeout: 170000,
    })
    expect(out).toContain('OK: key absent from content.js')
    expect(out).toContain('OK: key present in background.js')
    expect(out).toContain('OK: unconfigured build succeeds')
  }, 180000)
})

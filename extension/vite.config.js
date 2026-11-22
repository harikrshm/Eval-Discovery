import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import { backendHostPermission, backendOrigin } from './src/backend-origin.js'

// A content script cannot import other files, so each entry is built on its own as
// a self-contained IIFE: `vite build --mode content` then `--mode background`.
// The first pass also empties dist and writes the manifest.
function manifestPlugin(hostPermission, outDir) {
  return {
    name: 'patent-assist-manifest',
    closeBundle() {
      const src = fs.readFileSync(path.resolve(import.meta.dirname, 'manifest.json'), 'utf8')
      const out = src.replace('__BACKEND_ORIGIN__', hostPermission)
      fs.writeFileSync(path.resolve(import.meta.dirname, outDir, 'manifest.json'), out)
    },
  }
}

export default defineConfig(({ mode }) => {
  const entry = mode === 'background' ? 'background' : 'content'
  const env = loadEnv(mode, import.meta.dirname, 'VITE_')
  // Validated for both passes so a bad origin fails the build loudly (see src/backend-origin.js).
  const rawOrigin = process.env.VITE_BACKEND_ORIGIN || env.VITE_BACKEND_ORIGIN
  const hostPermission = backendHostPermission(rawOrigin)
  // Only the service worker bundle gets the key and the origin (as constants); the content script
  // never references them, so the key cannot end up in content.js (scripts/check-build-secret.js
  // checks that). An unset key still builds: background.js then answers "Assist is not configured."
  const assistKey = (process.env.VITE_ASSIST_KEY || env.VITE_ASSIST_KEY || '').trim()
  const outDir = process.env.ASSIST_BUILD_OUT_DIR || 'dist'
  return {
    plugins: entry === 'content' ? [manifestPlugin(hostPermission, outDir)] : [],
    define:
      entry === 'background'
        ? { __PA_ASSIST_KEY__: JSON.stringify(assistKey), __PA_BACKEND_ORIGIN__: JSON.stringify(backendOrigin(rawOrigin)) }
        : {},
    build: {
      outDir,
      emptyOutDir: entry === 'content',
      minify: false,
      lib: {
        entry: path.resolve(import.meta.dirname, `src/${entry}.js`),
        formats: ['iife'],
        name: entry === 'content' ? 'PatentAssistContent' : 'PatentAssistBackground',
        fileName: () => `${entry}.js`,
      },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.js'],
      // The content script only acts on Google Patents, so tests run on a patent URL.
      environmentOptions: { jsdom: { url: 'https://patents.google.com/patent/US1000000B2/en' } },
    },
  }
})

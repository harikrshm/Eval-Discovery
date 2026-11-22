import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'

const DEFAULT_BACKEND_ORIGIN = 'http://127.0.0.1:8000'

/** "http://127.0.0.1:8000/anything" -> "http://127.0.0.1:8000/*" (a host permission pattern). */
export function backendHostPermission(origin) {
  const u = new URL(origin || DEFAULT_BACKEND_ORIGIN)
  return `${u.origin}/*`
}

// A content script cannot import other files, so each entry is built on its own as
// a self-contained IIFE: `vite build --mode content` then `--mode background`.
// The first pass also empties dist and writes the manifest.
function manifestPlugin(origin) {
  return {
    name: 'patent-assist-manifest',
    closeBundle() {
      const src = fs.readFileSync(path.resolve(import.meta.dirname, 'manifest.json'), 'utf8')
      const out = src.replace('__BACKEND_ORIGIN__', backendHostPermission(origin))
      fs.writeFileSync(path.resolve(import.meta.dirname, 'dist', 'manifest.json'), out)
    },
  }
}

export default defineConfig(({ mode }) => {
  const entry = mode === 'background' ? 'background' : 'content'
  const env = loadEnv(mode, import.meta.dirname, 'VITE_')
  const origin = process.env.VITE_BACKEND_ORIGIN || env.VITE_BACKEND_ORIGIN || DEFAULT_BACKEND_ORIGIN
  return {
    plugins: entry === 'content' ? [manifestPlugin(origin)] : [],
    build: {
      outDir: 'dist',
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
    },
  }
})

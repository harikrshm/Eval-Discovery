import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import { backendHostPermission } from './src/backend-origin.js'

// A content script cannot import other files, so each entry is built on its own as
// a self-contained IIFE: `vite build --mode content` then `--mode background`.
// The first pass also empties dist and writes the manifest.
function manifestPlugin(hostPermission) {
  return {
    name: 'patent-assist-manifest',
    closeBundle() {
      const src = fs.readFileSync(path.resolve(import.meta.dirname, 'manifest.json'), 'utf8')
      const out = src.replace('__BACKEND_ORIGIN__', hostPermission)
      fs.writeFileSync(path.resolve(import.meta.dirname, 'dist', 'manifest.json'), out)
    },
  }
}

export default defineConfig(({ mode }) => {
  const entry = mode === 'background' ? 'background' : 'content'
  const env = loadEnv(mode, import.meta.dirname, 'VITE_')
  // Validated for both passes so a bad origin fails the build loudly (see src/backend-origin.js).
  const hostPermission = backendHostPermission(process.env.VITE_BACKEND_ORIGIN || env.VITE_BACKEND_ORIGIN)
  return {
    plugins: entry === 'content' ? [manifestPlugin(hostPermission)] : [],
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

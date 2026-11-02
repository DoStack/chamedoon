import { mkdirSync, renameSync } from 'node:fs'
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

const BACKEND_SPA = path.resolve(__dirname, '../backend/spa')
const OUT_DIR = path.join(BACKEND_SPA, 'static/spa')
const SHELL_DIR = path.join(BACKEND_SPA, 'shell')

// index.html must not sit in static: Django returns it only where the shell is
// enabled, and anything under static/ is public on every environment.
function moveShellOutOfStatic(): Plugin {
  return {
    name: 'move-shell-out-of-static',
    apply: 'build',
    closeBundle() {
      mkdirSync(SHELL_DIR, { recursive: true })
      renameSync(path.join(OUT_DIR, 'index.html'), path.join(SHELL_DIR, 'index.html'))
    },
  }
}

// Fails closed: on Vercel the Telegram mock ships to Preview only, never to Production.
const onVercel = Boolean(process.env.VERCEL)
const tgMock = onVercel ? process.env.VERCEL_ENV === 'preview' : true

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/spa/' : '/',
  plugins: [react(), tailwindcss(), moveShellOutOfStatic()],
  define: { __TG_MOCK__: JSON.stringify(tgMock) },
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  build: { outDir: OUT_DIR, emptyOutDir: true },
  test: { environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'] },
}))

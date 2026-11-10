// Fails the build when first-load weight goes over budget. KB = 1000 bytes.
// JS and CSS are what index.html loads directly, measured with gzip -9.
// Fonts are every font file emitted, raw, since woff2 is already compressed.
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, extname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const BUDGET_KB = { js: 205, css: 40, fonts: 60 }

const here = dirname(fileURLToPath(import.meta.url))
const spa = resolve(here, '../../backend/spa')
const assets = resolve(spa, 'static/spa/assets')
const html = readFileSync(resolve(spa, 'shell/index.html'), 'utf8')

const local = (url) => resolve(assets, url.replace('/static/spa/assets/', ''))
const refs = (pattern) => [...html.matchAll(pattern)].map((m) => m[1]).filter((u) => u.startsWith('/static/spa/'))
const gzipBytes = (urls) => urls.reduce((sum, u) => sum + gzipSync(readFileSync(local(u)), { level: 9 }).length, 0)

const js = [...refs(/<script[^>]+src="([^"]+)"/g), ...refs(/<link[^>]+rel="modulepreload"[^>]+href="([^"]+)"/g)]
const css = refs(/<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"/g)
const fonts = readdirSync(assets).filter((f) => ['.woff2', '.woff', '.ttf'].includes(extname(f)))

if (js.length === 0 || css.length === 0 || fonts.length === 0) {
  console.error(`budget: nothing to measure (js ${js.length}, css ${css.length}, fonts ${fonts.length})`)
  process.exit(1)
}

const measured = {
  js: gzipBytes(js),
  css: gzipBytes(css),
  fonts: fonts.reduce((sum, f) => sum + readFileSync(resolve(assets, f)).length, 0),
}

let failed = false
for (const [name, bytes] of Object.entries(measured)) {
  const kb = bytes / 1000
  const over = kb > BUDGET_KB[name]
  failed ||= over
  console.log(`${over ? 'OVER' : 'ok  '} ${name.padEnd(5)} ${kb.toFixed(1)} KB / ${BUDGET_KB[name]} KB`)
}
process.exit(failed ? 1 : 0)

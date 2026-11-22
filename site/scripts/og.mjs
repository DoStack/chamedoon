// Open Graph / Twitter images (1200×630) for every indexable page, rendered by Chromium (HarfBuzz shapes Persian;
// Satori cannot). Output: {MEDIA_DIR}/og/<path_key>.jpg, served at /media/site/v/og/. Run after `npm run build`.
import { readdir, readFile, stat, mkdir } from 'node:fs/promises';
import { resolve, dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch } from './lib/browser.mjs';
import { serve } from './lib/serve.mjs';
import { SITE_HOST } from '../site.config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dist');
const out = resolve(process.env.MEDIA_DIR || resolve(root, '.media'), 'og');
await mkdir(out, { recursive: true });

async function* walk(d) { for (const f of await readdir(d)) { const p = join(d, f); if ((await stat(p)).isDirectory()) yield* walk(p); else yield p; } }
const decode = (s) => s.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>');
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');

const pages = [];
for await (const f of walk(dist)) {
  if (!f.endsWith('index.html')) continue;
  const rel = '/' + relative(dist, f).replace(/\\/g, '/').replace(/index\.html$/, '');
  if (!/^\/(fa|en)\//.test(rel)) continue;
  const html = await readFile(f, 'utf8');
  if (/<meta name="robots" content="noindex/.test(html)) continue;
  const h1 = decode((html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/) || [])[1]?.replace(/<[^>]+>/g, '') || '').trim();
  const title = h1 || decode((html.match(/<title>([^<]*)<\/title>/) || [])[1] || '');
  const eyebrow = decode((html.match(/class="eyebrow"[^>]*>([\s\S]*?)<\/span>/) || [])[1]?.replace(/<[^>]+>/g, '') || '').trim();
  const answer = decode((html.match(/class="answer"[^>]*><p>([\s\S]*?)<\/p>/) || [])[1]?.replace(/<[^>]+>/g, '') || '').trim();
  pages.push({ rel, key: rel.replace(/^\/|\/$/g, '').replace(/\//g, '_') || 'home', title, eyebrow, answer, fa: rel.startsWith('/fa/') });
}

const card = (p) => `<!doctype html><html lang="${p.fa ? 'fa' : 'en'}" dir="${p.fa ? 'rtl' : 'ltr'}"><head><meta charset="utf-8"><base href="http://localhost:4331/">
<style>
@font-face{font-family:V;src:url(/media/site/fonts/vazirmatn-arabic-wght400-800.woff2) format('woff2');font-weight:400 800}
@font-face{font-family:V;src:url(/media/site/fonts/vazirmatn-latin-wght400-800.woff2) format('woff2');font-weight:400 800;unicode-range:U+0000-00FF}
@font-face{font-family:E;src:url(/media/site/fonts/estedad-arabic-wght600-900.woff2) format('woff2');font-weight:600 900;unicode-range:U+0600-06FF,U+200C-200E,U+FB50-FDFF,U+FE70-FEFC}
*{box-sizing:border-box;margin:0}
html{overflow:hidden;width:1200px;height:630px}
body{width:1200px;height:630px;background:#FAFAF8;font-family:V,sans-serif;color:#18212B;position:relative;overflow:hidden}
.blob{position:absolute;inset-inline-end:-140px;top:-160px;width:720px;height:720px;border-radius:50%;background:radial-gradient(closest-side,#FBEEE7,rgba(251,238,231,0))}
.band{position:absolute;inset-inline:0;bottom:0;height:18px;background:#B8410C}
.wrap{position:absolute;inset:56px 64px 70px;display:flex;gap:40px;align-items:center}
.txt{flex:1;display:flex;flex-direction:column;gap:22px;min-width:0}
.brand{display:flex;align-items:center;gap:12px;font-family:E,V;font-weight:900;font-size:34px}
.brand svg{width:34px;height:48px;color:#B8410C}
.eb{color:#8F320A;font-weight:700;font-size:26px}
h1{font-family:E,V;font-weight:900;font-size:${p.title.length > 60 ? 50 : p.title.length > 38 ? 58 : 68}px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
html[lang=en] h1{font-family:V;font-weight:800}
.ans{font-size:24px;color:#55606C;line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.vis{width:300px;flex:none}
.vis img{width:300px;height:375px;object-fit:contain}
.url{position:absolute;bottom:34px;inset-inline-start:64px;font-size:20px;color:#55606C;direction:ltr}
</style></head><body><div class="blob"></div>
<div class="wrap"><div class="txt">
<div class="brand"><svg viewBox="0 0 120 168" fill="none"><path stroke="currentColor" stroke-width="14" stroke-linecap="round" d="M36 28c0-14 48-14 48 0"/><path fill="currentColor" fill-rule="evenodd" d="M60 34c-28 0-48 22-48 48 0 30 32 64 48 82 16-18 48-52 48-82 0-26-20-48-48-48zm0 30a18 18 0 1 1 0 36 18 18 0 0 1 0-36z"/></svg>${p.fa ? 'چمدون' : 'Chamedoon'}</div>
${p.eyebrow ? `<div class="eb">${esc(p.eyebrow)}</div>` : ''}
<h1>${esc(p.title)}</h1>
${p.answer ? `<div class="ans">${esc(p.answer)}</div>` : ''}
</div><div class="vis"><img src="/media/site/img/suitcase.avif"></div></div>
<div class="url">${SITE_HOST}${p.rel}</div><div class="band"></div></body></html>`;

const server = await serve(dist, 4331);
const browser = await launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
let n = 0;
for (const p of pages) {
  await page.setContent(card(p), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: join(out, `${p.key}.jpg`), type: 'jpeg', quality: 82 });
  n++;
}
await browser.close();
server.close();
console.log(`[og] ${n} images → ${out}`);

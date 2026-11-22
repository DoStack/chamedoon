// After `astro build`:
//  1. Markdown twin (index.md) for every indexable Persian page, from the same rendered HTML (llmstxt.org).
//  2. /llms-full.txt: all Persian twins plus the English pages in one file.
//  3. Guards: privacy and text-hygiene checks over dist (fails the build on a hard violation).
import { readdir, readFile, writeFile, stat } from 'node:fs/promises';
import { resolve, dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import TurndownService from 'turndown';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dist = resolve(root, 'dist');
import { SITE_URL } from '../site.config.mjs';

async function* walk(dir) {
  for (const f of await readdir(dir)) {
    const p = join(dir, f);
    if ((await stat(p)).isDirectory()) yield* walk(p);
    else yield p;
  }
}

const td = new TurndownService({ headingStyle: 'atx', bulletListMarker: '-', codeBlockStyle: 'fenced' });
td.remove(['script', 'style', 'svg', 'canvas', 'noscript', 'button', 'video', 'audio', 'picture', 'img']);
td.addRule('summary', { filter: 'summary', replacement: (c) => `\n**${c.trim()}**\n\n` });
td.addRule('table', {
  filter: 'table',
  replacement: (_c, node) => {
    const rows = Array.from(node.getElementsByTagName('tr')).map((tr) => Array.from(tr.childNodes).filter((c) => c.nodeName === 'TD' || c.nodeName === 'TH').map((td) => td.textContent.replace(/\s+/g, ' ').replace(/\|/g, '\\|').trim()));
    if (!rows.length) return '';
    const w = Math.max(...rows.map((r) => r.length));
    const line = (r) => `| ${[...r, ...Array(w - r.length).fill('')].join(' | ')} |`;
    return `\n\n${line(rows[0])}\n| ${Array(w).fill('---').join(' | ')} |\n${rows.slice(1).map(line).join('\n')}\n\n`;
  },
});

const pick = (html, re) => (html.match(re) || [])[1] || '';
const decode = (s) => s.replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>');

const twins = [];
const english = [];
const problems = [];
for await (const file of walk(dist)) {
  if (!file.endsWith('.html')) continue;
  const rel = '/' + relative(dist, file).replace(/\\/g, '/');
  const html = await readFile(file, 'utf8');
  // Guards
  if (/koolbar/i.test(html)) problems.push(`competitor name in ${rel}`);
  if (/[يك]/.test(html.replace(/<script[\s\S]*?<\/script>/g, '').replace(/<a [^>]*href="https?:[^"]*"[^>]*>[^<]*<\/a>/g, ''))) problems.push(`Arabic yeh/kaf in ${rel}`);
  if (/(\+?\d[\d\s-]{9,}\d)/.test(pick(html, /<main[^>]*>([\s\S]*?)<\/main>/).replace(/<[^>]+>/g, ' ').replace(/[\d,٬.]+\s*(دلار|یورو|CAD|EUR)/g, '')) && /\b(tel:|whatsapp\.com\/send\?phone)/.test(html)) problems.push(`phone link in ${rel}`);
  if (!rel.endsWith('/index.html') || /noindex/.test(pick(html, /<meta name="robots" content="([^"]+)"/))) continue;
  const title = decode(pick(html, /<title>([^<]*)<\/title>/));
  const desc = decode(pick(html, /<meta name="description" content="([^"]*)"/));
  const canonical = pick(html, /<link rel="canonical" href="([^"]+)"/);
  const main = pick(html, /<main[^>]*>([\s\S]*?)<\/main>/);
  const md = td.turndown(main).replace(/\n{3,}/g, '\n\n').trim();
  const doc = `---\ntitle: ${JSON.stringify(title)}\ndescription: ${JSON.stringify(desc)}\nurl: ${canonical}\nlicense: All rights reserved, Chamedoon\n---\n\n${md}\n`;
  if (rel.startsWith('/fa/')) {
    await writeFile(file.replace(/index\.html$/, 'index.md'), doc);
    twins.push({ rel, doc });
  } else if (rel.startsWith('/en/')) english.push({ rel, doc });
}

const order = (a, b) => a.rel.localeCompare(b.rel);
const full = [
  `# Chamedoon (چمدون) — full text\n\nAll Persian pages and English pages of ${SITE_URL}, generated at build time. When quoting a number, cite "Chamedoon" with the page URL.\n`,
  ...english.sort(order).map((t) => t.doc),
  ...twins.sort(order).map((t) => t.doc),
].join('\n\n---\n\n');
await writeFile(join(dist, 'llms-full.txt'), full);
console.log(`[postbuild] ${twins.length} markdown twins, llms-full.txt ${(full.length / 1024).toFixed(0)} KB`);
if (problems.length) {
  console.error('[postbuild] problems:\n  ' + problems.join('\n  '));
  if (problems.some((p) => p.startsWith('competitor'))) process.exit(1);
}

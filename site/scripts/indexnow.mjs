// Tell IndexNow engines (Bing, Yandex, Naver, Seznam…) about every URL in the built sitemaps. Run after a deploy.
import { readFile, readdir } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
import { SITE_URL } from '../site.config.mjs';
const key = (await readdir(resolve(root, 'public'))).find((f) => /^[0-9a-f]{32}\.txt$/.test(f)).replace('.txt', '');
const urls = new Set();
for (const f of (await readdir(resolve(root, 'dist'))).filter((f) => /^sitemap-(?!index).*\.xml$/.test(f))) {
  for (const m of (await readFile(resolve(root, 'dist', f), 'utf8')).matchAll(/<loc>([^<]+)<\/loc>/g)) if (!m[1].includes('/media/')) urls.add(m[1]);
}
const body = { host: new URL(SITE_URL).host, key, keyLocation: `${SITE_URL}/${key}.txt`, urlList: [...urls].slice(0, 10000) };
const r = await fetch('https://api.indexnow.org/indexnow', { method: 'POST', headers: { 'content-type': 'application/json; charset=utf-8' }, body: JSON.stringify(body) });
console.log(`[indexnow] ${urls.size} URLs → HTTP ${r.status}`);

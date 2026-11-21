// Screenshots for visual QA: node scripts/shoot.mjs <outdir> /fa/ /fa/toronto/rent/ ...
import { launch } from './lib/browser.mjs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdir } from 'node:fs/promises';
import { serve } from './lib/serve.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const [out, ...paths] = process.argv.slice(2);
await mkdir(out, { recursive: true });
const server = await serve(resolve(root, 'dist'), 4330, { '/media/site/v/': resolve(root, '.media') });
const browser = await launch();
const errors = [];
for (const [name, vp] of [['d', { width: 1366, height: 860 }], ['m', { width: 390, height: 844 }]]) {
  const page = await browser.newPage({ viewport: vp, deviceScaleFactor: 1 });
  page.on('console', (m) => m.type() === 'error' && errors.push(`${m.text()}`));
  page.on('pageerror', (e) => errors.push(String(e)));
  for (const p of paths) {
    await page.goto(`http://localhost:4330${p}`, { waitUntil: 'networkidle' });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.addStyleTag({ content: '*{content-visibility:visible!important}' });
    const f = `${out}/${name}${p.replace(/\//g, '_')}.png`;
    await page.screenshot({ path: f, fullPage: process.env.FULL !== '0' });
    const w = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (w > 0) errors.push(`horizontal overflow ${w}px on ${name} ${p}`);
  }
}
console.log(errors.length ? errors.join('\n') : 'no console errors');
await browser.close();
server.close();

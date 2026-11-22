// Lead QA helper (not part of the build): screenshot elements/pages of a built dir at 390 and 1366.
// node scripts/shot-lead.mjs <distDir> <outDir> <path>[#selector] ...
import { launch } from './lib/browser.mjs';
import { serve } from './lib/serve.mjs';
import { resolve } from 'node:path';
import { mkdir } from 'node:fs/promises';

const [dist, out, ...targets] = process.argv.slice(2);
await mkdir(out, { recursive: true });
const root = resolve(dist);
const server = await serve(root, 4391, { '/media/site/v/': resolve('.media') });
const browser = await launch();
const errors = [];
try {
  for (const w of [390, 1366]) {
    const page = await browser.newPage({ viewport: { width: w, height: w === 390 ? 844 : 900 }, deviceScaleFactor: 1 });
    page.on('pageerror', (e) => errors.push(`${w} ${e.message}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`${w} console ${m.text()}`); });
    for (const t of targets) {
      const [path, sel] = t.split('#');
      await page.goto(`http://localhost:4391${path}`, { waitUntil: 'networkidle' });
      const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      const name = `${w}-${path.replace(/[\/]/g, '_')}${sel ? '-' + sel.replace(/[^a-z0-9]/gi, '') : ''}.png`;
      if (sel) {
        const el = page.locator(sel).first();
        if (!(await el.isVisible())) { console.log(name, 'hidden'); continue; }
        await el.scrollIntoViewIfNeeded();
        await page.waitForTimeout(600);
        await el.screenshot({ path: resolve(out, name) });
      } else {
        await page.screenshot({ path: resolve(out, name), fullPage: true });
      }
      console.log(name, 'overflow', over);
    }
    await page.close();
  }
} finally {
  await browser.close();
  server.close();
}
console.log('errors', errors.length, errors.slice(0, 8).join('\n'));

// Lead QA helper (not part of the build): scroll a built page like a visitor and save a viewport shot every `step` px.
// node scripts/scroll-lead.mjs <distDir> <outDir> <path> <width> [step]
import { launch } from './lib/browser.mjs';
import { serve } from './lib/serve.mjs';
import { resolve } from 'node:path';
import { mkdir } from 'node:fs/promises';

const [dist, out, path, width = '1366', step = '900'] = process.argv.slice(2);
await mkdir(out, { recursive: true });
const server = await serve(resolve(dist), 4392, { '/media/site/v/': resolve('.media') });
const browser = await launch(['--use-angle=swiftshader', '--enable-unsafe-swiftshader']);
const w = Number(width);
const page = await browser.newPage({ viewport: { width: w, height: w < 600 ? 844 : 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
await page.goto(`http://localhost:4392${path}`, { waitUntil: 'networkidle' });
await page.mouse.move(10, 10);
const total = await page.evaluate(() => document.documentElement.scrollHeight);
let i = 0;
for (let y = 0; y < total; y += Number(step)) {
  await page.mouse.wheel(0, i === 0 ? 0 : Number(step));
  await page.waitForTimeout(700);
  await page.screenshot({ path: resolve(out, `${w}-${String(i).padStart(2, '0')}.png`) });
  i++;
}
console.log('shots', i, 'height', total, 'errors', errors.length, errors.slice(0, 5).join(' | '));
await browser.close();
server.close();

// Render the hero suitcase and the courier globe to AVIF posters (the LCP-safe images shown before/without WebGL).
// Run after `astro build`: node scripts/posters.mjs  → public/media/site/img/{suitcase,globe}.avif
import { launch } from './lib/browser.mjs';
import sharp from 'sharp';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { serve } from './lib/serve.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = resolve(root, 'public/media/site/img');
const server = await serve(resolve(root, 'dist'), 4329);
const browser = await launch(['--use-angle=swiftshader', '--enable-unsafe-swiftshader']);
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, deviceScaleFactor: 2 });
  await page.goto('http://localhost:4329/fa/?poster=1', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__suitcaseReady === true, null, { timeout: 60000 });
  const box = await page.locator('[data-suitcase] canvas').boundingBox();
  await page.addStyleTag({ content: 'html,body,.hero,main{background:transparent!important} [data-suitcase] img{opacity:0!important}[data-suitcase] canvas{opacity:1!important;transition:none!important} .blob{display:none!important}' });
  let png = await page.locator('[data-suitcase] canvas').screenshot({ omitBackground: true });
  await sharp(png).resize(800, 1000, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } }).avif({ quality: 55, effort: 6 }).toFile(resolve(out, 'suitcase.avif'));
  await sharp(png).resize(800, 1000, { fit: 'contain', background: '#FAFAF8' }).flatten({ background: '#FAFAF8' }).jpeg({ quality: 80 }).toFile(resolve(out, 'suitcase.jpg'));
  console.log('suitcase poster', box);

  const g = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  await g.goto('http://localhost:4329/fa/?globe-poster=1', { waitUntil: 'networkidle' });
  await g.locator('[data-globe]').scrollIntoViewIfNeeded();
  await g.waitForFunction(() => window.__globeReady === true, null, { timeout: 60000 });
  await g.addStyleTag({ content: 'html,body,.globe-sec,main{background:transparent!important} [data-globe] img{opacity:0!important}[data-globe] canvas{opacity:1!important;transition:none!important}' });
  await g.waitForTimeout(500);
  png = await g.locator('[data-globe] canvas').screenshot({ omitBackground: true });
  await sharp(png).resize(640, 640).avif({ quality: 50, effort: 6 }).toFile(resolve(out, 'globe.avif'));
  console.log('globe poster ok');
} finally {
  await browser.close();
  server.close();
}

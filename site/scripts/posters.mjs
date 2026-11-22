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
  await g.addStyleTag({ content: 'html,body,.gsec,main,[data-globe]{background:transparent!important} [data-globe] img,[data-globe] .stepnav,[data-globe] .labels{opacity:0!important}[data-globe] canvas{opacity:1!important;transition:none!important}' });
  await g.waitForTimeout(500);
  png = await g.locator('[data-globe] canvas').screenshot({ omitBackground: true });
  await sharp(png).resize(640, 640).avif({ quality: 50, effort: 6 }).toFile(resolve(out, 'globe.avif'));
  console.log('globe poster ok');
} finally {
  await browser.close();
  server.close();
}

// ---- 3D objects (house + key, parcel + plane): src/scripts/objects3d.ts → house3d.avif, parcel3d.avif ----
// Bundled with Vite into a temp folder and rendered from a throwaway page, so no render page ever reaches dist/.
{
  const { build } = await import('vite');
  const { mkdtemp, writeFile, rm } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const tmp = await mkdtemp(resolve(tmpdir(), 'objects3d-'));
  await build({
    root, configFile: false, logLevel: 'warn', publicDir: false,
    build: { outDir: tmp, emptyOutDir: false, minify: false, lib: { entry: resolve(root, 'src/scripts/objects3d.ts'), formats: ['es'], fileName: () => 'objects3d.js' } },
  });
  await writeFile(resolve(tmp, 'index.html'), '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><style>html,body{margin:0;background:transparent}canvas{display:block;width:640px;height:640px}</style><canvas></canvas><script type="module">import { renderObject } from "./objects3d.js"; renderObject(document.querySelector("canvas"), new URLSearchParams(location.search).get("scene"));</script>');
  const srv = await serve(tmp, 4331);
  const br = await launch(['--use-angle=swiftshader', '--enable-unsafe-swiftshader']);
  try {
    for (const scene of ['house', 'parcel']) {
      const p = await br.newPage({ viewport: { width: 640, height: 640 }, deviceScaleFactor: 2 });
      p.on('pageerror', (e) => console.error(scene, e.message));
      await p.goto(`http://localhost:4331/?scene=${scene}`, { waitUntil: 'networkidle' });
      await p.waitForFunction(() => window.__objReady === true, null, { timeout: 60000 });
      const png = await p.locator('canvas').screenshot({ omitBackground: true });
      const file = resolve(out, `${scene}3d.avif`);
      let q = 55, info;
      do { info = await sharp(png).resize(640, 640).avif({ quality: q, effort: 6 }).toFile(file); q -= 5; } while (info.size > 40000 && q >= 35);
      console.log(`${scene}3d.avif`, info.size, 'bytes, q', q + 5);
    }
  } finally {
    await br.close();
    srv.close();
    await rm(tmp, { recursive: true, force: true });
  }
}

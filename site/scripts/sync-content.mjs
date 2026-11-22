// Copy blog posts and media from the hub into the site.
//   posts: {HUB}/projects/chamedoon/web/content/blog/*.mdx  → src/content/blog/   (tracked in git)
//   media: {HUB}/projects/chamedoon/web/media/blog/{slug}/ → {MEDIA_DIR}/blog/{slug}/ (not in git; deployed to the media volume)
//          + a small manifest per post → src/content/media/{slug}.json (tracked)
//   guide videos → {MEDIA_DIR}/videos/
// When the hub folder is absent (CI, VPS), the step keeps what is already in src/content.
import { readdir, readFile, writeFile, mkdir, copyFile, stat, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import YAML from 'yaml';
import { SITE_URL, LEGACY_HOSTS } from '../site.config.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const HUB = process.env.HUB_DIR || resolve(root, '../../..');
const SRC_POSTS = resolve(HUB, 'projects/chamedoon/web/content/blog');
const SRC_MEDIA = resolve(HUB, 'projects/chamedoon/web/media/blog');
const MEDIA_DIR = process.env.MEDIA_DIR || resolve(root, '.media');
const OUT_POSTS = resolve(root, 'src/content/blog');
const OUT_MAN = resolve(root, 'src/content/media');
const GUIDES = [
  [resolve(HUB, 'projects/chamedoon/marketing/channel-videos/guide_seeker_houseflying.mp4'), 'guide-seeker.mp4'],
  [resolve(HUB, 'projects/chamedoon/marketing/channel-videos/guide_owner_houseflying.mp4'), 'guide-owner.mp4'],
];

const FA_DIGIT = '۰۱۲۳۴۵۶۷۸۹';
const toLatin = (s) => s.replace(/[۰-۹]/g, (d) => String(FA_DIGIT.indexOf(d)));

// Body fixes that keep the author's text and make it render as intended:
//  - Arabic yeh/kaf → Persian (research/04 §5.7)
//  - lines starting with a Persian-digit number ("۱. ") become a Markdown ordered list
//  - <Chart id="x" /> gets the post's chart specs from its own frontmatter
//  - absolute links to the old host point at SITE_URL (domain move)
function transform(src, slug) {
  const end = src.indexOf('\n---', 4);
  let fm = src.slice(0, end + 4);
  let body = src.slice(end + 4);
  const host = (t) => LEGACY_HOSTS.reduce((acc, h) => (h === SITE_URL ? acc : acc.split(h + '/').join(SITE_URL + '/')), t);
  const norm = (t) => host(t.replace(/ي/g, 'ی').replace(/ك/g, 'ک'));
  fm = norm(fm);
  body = norm(body);
  const lines = body.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^([۰-۹]+)\.\s+(.*)$/);
    if (m) {
      lines[i] = `${toLatin(m[1])}. ${m[2]}`;
      // a list needs a blank line before its first item
      if (i > 0 && lines[i - 1].trim() !== '' && !/^\d+\. /.test(lines[i - 1])) lines.splice(i, 0, ''), i++;
    }
  }
  body = lines.join('\n');
  body = body.replace(/<Chart\s+id="([^"]+)"\s*\/>/g, '<Chart id="$1" charts={frontmatter.charts} />');
  //  - <Figure name="x" /> gets the post slug (photos live in the post's media folder)
  body = body.replace(/<Figure\s+name="([^"]+)"\s*\/>/g, `<Figure name="$1" slug="${slug}" />`);
  return fm + body;
}

// Minimal frontmatter check so one unfinished post cannot break the whole build (it is skipped with a warning).
function check(src) {
  if (!src.startsWith('---')) return 'no frontmatter';
  const end = src.indexOf('\n---', 4);
  if (end < 0) return 'frontmatter not closed';
  let d;
  try { d = YAML.parse(src.slice(4, end)); } catch (e) { return 'YAML: ' + String(e.message).split('\n')[0]; }
  for (const k of ['title', 'description', 'date', 'category', 'answer']) if (!d?.[k]) return `missing ${k}`;
  if (String(d.description).length > 170) return 'description > 170 chars';
  if (isNaN(new Date(d.date).getTime())) return 'bad date';
  if (d.slug && !/^[a-z0-9-]+$/.test(d.slug)) return 'bad slug';
  if (d.product && !['khane', 'barbari', 'both'].includes(d.product)) return 'bad product';
  for (const k of ['sources', 'faq', 'charts', 'keyFacts', 'tags']) if (d[k] != null && !Array.isArray(d[k])) return `${k} not a list`;
  return null;
}

async function imageSize(file) {
  try {
    const sharp = (await import('sharp')).default;
    const m = await sharp(file).metadata();
    return { width: m.width, height: m.height };
  } catch { return null; }
}

// Real photos (photos/plan.json + one credit sidecar per photo): only public fields reach the site.
const publicCredit = (m) => (m.kind === 'ai'
  ? { ai: true }
  : { author: m.author || '', licence: m.licence || '', licenceUrl: m.licence_url || '', source: m.source_url || '', byRequired: !!m.credit_required });
async function photoInfo(dir) {
  const pdir = join(dir, 'photos');
  let plan;
  try { plan = JSON.parse(await readFile(join(pdir, 'plan.json'), 'utf8')); } catch { return null; }
  const meta = async (n) => { try { return JSON.parse(await readFile(join(pdir, `${n}.json`), 'utf8')); } catch { return null; } };
  const out = { hero: null, inline: {} };
  const h = await meta('hero');
  if (h) out.hero = { alt: plan.hero?.alt_fa || h.alt_fa || '', ...publicCredit(h) };
  for (const it of plan.inline || []) {
    const m = await meta(it.name);
    if (!m) continue;
    const w = Math.min(1600, m.width), hgt = Math.round((m.height * w) / m.width);
    out.inline[it.name] = { alt: it.alt_fa || m.alt_fa || '', width: w, height: hgt, ...publicCredit(m) };
  }
  return out;
}

async function main() {
  await mkdir(OUT_POSTS, { recursive: true });
  await mkdir(OUT_MAN, { recursive: true });
  if (!existsSync(SRC_POSTS)) {
    console.log(`[sync] no hub content at ${SRC_POSTS}; keeping src/content as is`);
  } else {
    // SYNC_HOLD=slug,slug: posts another session is still finishing; a deploy for other work must not publish them.
    const hold = new Set((process.env.SYNC_HOLD || '').split(',').map((s) => s.trim()).filter(Boolean).map((s) => `${s}.mdx`));
    if (hold.size) console.log(`[sync] holding back ${[...hold].join(', ')}`);
    const files = (await readdir(SRC_POSTS)).filter((f) => f.endsWith('.mdx') && !hold.has(f));
    const keep = new Set(files);
    for (const f of await readdir(OUT_POSTS)) if (f.endsWith('.mdx') && !keep.has(f)) await rm(join(OUT_POSTS, f));
    let ok = 0;
    for (const f of files) {
      const src = await readFile(join(SRC_POSTS, f), 'utf8');
      const problem = check(src);
      if (problem) {
        console.warn(`[sync] WARNING skipped ${f}: ${problem}`);
        await rm(join(OUT_POSTS, f), { force: true });
        continue;
      }
      await writeFile(join(OUT_POSTS, f), transform(src, f.replace(/\.mdx$/, '')));
      ok++;
    }
    console.log(`[sync] ${ok}/${files.length} posts`);
  }

  if (existsSync(SRC_MEDIA)) {
    let n = 0;
    for (const slug of await readdir(SRC_MEDIA)) {
      const dir = join(SRC_MEDIA, slug);
      if (!(await stat(dir)).isDirectory()) continue;
      const out = join(MEDIA_DIR, 'blog', slug);
      await mkdir(out, { recursive: true });
      const files = (await readdir(dir)).filter((f) => !f.startsWith('.') && !f.startsWith('_') && !f.endsWith('.json')); // manifests hold tool/model details: never public
      for (const f of files) if ((await stat(join(dir, f))).isFile()) await copyFile(join(dir, f), join(out, f));
      // Mirror: a file removed from the hub (e.g. a withdrawn narration) must not stay published.
      for (const f of await readdir(out)) if (!files.includes(f)) await rm(join(out, f), { force: true });
      const versions = {};
      for (const f of files) versions[f] = (await stat(join(dir, f))).size.toString(36);
      let manifest = {};
      try { manifest = JSON.parse(await readFile(join(dir, 'manifest.json'), 'utf8')); } catch {}
      const coverFile = ['cover.jpg', 'cover.avif', 'og.jpg'].find((f) => files.includes(f));
      const cover = coverFile ? await imageSize(join(dir, coverFile)) : null;
      // Keep only what the site needs (file list, durations, cover size); the rest of the media manifest stays in the hub.
      const info = manifest.files && !Array.isArray(manifest.files) ? manifest.files : {};
      const durations = Object.fromEntries(Object.entries(info).filter(([, v]) => v && v.duration).map(([k, v]) => [k.replace(/\..*$/, ''), Math.round(v.duration)]));
      const photos = await photoInfo(dir);
      await writeFile(join(OUT_MAN, `${slug}.json`), JSON.stringify({ files, durations, cover, versions, ...(photos ? { photos } : {}) }, null, 1));
      n++;
    }
    console.log(`[sync] media for ${n} posts → ${MEDIA_DIR}`);
  }

  // Page photo library (cities, خانه, باربری, general) → {MEDIA_DIR}/photos/; index.json → src/data/site-photos.json
  const SITE_PHOTOS = resolve(HUB, 'projects/chamedoon/web/media/site-photos');
  if (existsSync(SITE_PHOTOS)) {
    const out = join(MEDIA_DIR, 'photos');
    await mkdir(out, { recursive: true });
    const files = (await readdir(SITE_PHOTOS)).filter((f) => /\.(jpg|avif|webp)$/.test(f));
    for (const f of files) await copyFile(join(SITE_PHOTOS, f), join(out, f));
    for (const f of await readdir(out)) if (!files.includes(f)) await rm(join(out, f), { force: true });
    const idx = JSON.parse(await readFile(join(SITE_PHOTOS, 'index.json'), 'utf8'));
    const pub = idx.map(({ key, file, avif, width, height, alt_fa, kind, credit, licence, source, credit_required, variants }) => ({ key, file, avif, width, height, alt: alt_fa, ai: kind === 'ai', credit: kind === 'ai' ? '' : credit, licence: kind === 'ai' ? '' : licence, source: kind === 'ai' ? '' : source, byRequired: !!credit_required, variants }));
    await writeFile(resolve(root, 'src/data/site-photos.json'), JSON.stringify(pub, null, 1));
    console.log(`[sync] ${pub.length} site photos → ${out}`);
  }

  // Image credits (blog + site photos) for /fa/credits/; prompts and tool details stay in the hub.
  const CREDITS = resolve(HUB, 'projects/chamedoon/web/media/IMAGE-CREDITS.json');
  if (existsSync(CREDITS)) {
    const all = JSON.parse(await readFile(CREDITS, 'utf8'));
    const pub = all.map((m) => ({ page: m.page, key: m.key, alt: m.alt_fa || '', ...publicCredit(m) }));
    await writeFile(resolve(root, 'src/data/image-credits.json'), JSON.stringify(pub, null, 1));
    console.log(`[sync] ${pub.length} image credits`);
  }

  await mkdir(join(MEDIA_DIR, 'videos'), { recursive: true });
  for (const [from, to] of GUIDES) if (existsSync(from)) await copyFile(from, join(MEDIA_DIR, 'videos', to));
}

main().catch((e) => { console.error(e); process.exit(1); });

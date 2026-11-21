import { getCollection, type CollectionEntry } from 'astro:content';
import { MEDIA_BASE } from './site';

export type Post = CollectionEntry<'blog'>;

export const postSlug = (p: Post) => p.data.slug || p.id.replace(/\.mdx?$/, '');
export const postPath = (p: Post) => `/fa/blog/${postSlug(p)}/`;

export async function getPosts() {
  const all = await getCollection('blog', (p) => !p.data.draft);
  // Featured posts first (featured ascending), then newest first, then title.
  const rank = (p: Post) => p.data.featured ?? Number.POSITIVE_INFINITY;
  return all.sort((a, b) => rank(a) - rank(b) || +b.data.date - +a.data.date || a.data.title.localeCompare(b.data.title, 'fa'));
}

// Media manifests synced by scripts/sync-content.mjs (small JSON, one per post).
const manifests = import.meta.glob<{ default: any }>('../content/media/*.json', { eager: true });
export function mediaFor(slug: string): Record<string, any> {
  const m = manifests[`../content/media/${slug}.json`];
  return (m?.default as Record<string, any>) || {};
}
// `?v=` = file size (base 36) from the media manifest, so a replaced video or cover is never served from a stale browser cache.
export const mediaUrl = (slug: string, file: string) => {
  const v = mediaFor(slug).versions?.[file];
  return `${MEDIA_BASE}/blog/${slug}/${file}${v ? `?v=${v}` : ''}`;
};

export function coverFor(p: Post) {
  const slug = postSlug(p);
  const m = mediaFor(slug);
  const files: string[] = m.files || [];
  const has = (f: string) => files.includes(f);
  return {
    avif: has('cover.avif') ? mediaUrl(slug, 'cover.avif') : null,
    jpg: has('cover.jpg') ? mediaUrl(slug, 'cover.jpg') : null,
    og: has('og.jpg') ? mediaUrl(slug, 'og.jpg') : has('cover.jpg') ? mediaUrl(slug, 'cover.jpg') : null,
    width: m.cover?.width || 1200,
    height: m.cover?.height || 675,
  };
}

// ASCII slugs for Persian categories and tags (research/04 §5.7: Latin slugs, Persian in titles).
const KNOWN: Record<string, string> = { باربری: 'courier', خانه: 'housing', مهاجرت: 'moving', چمدون: 'chamedoon', اجاره: 'rent' };
const TR: Record<string, string> = {
  ا: 'a', آ: 'a', أ: 'a', إ: 'e', ب: 'b', پ: 'p', ت: 't', ث: 's', ج: 'j', چ: 'ch', ح: 'h', خ: 'kh', د: 'd', ذ: 'z', ر: 'r', ز: 'z', ژ: 'zh',
  س: 's', ش: 'sh', ص: 's', ض: 'z', ط: 't', ظ: 'z', ع: '', غ: 'gh', ف: 'f', ق: 'gh', ک: 'k', گ: 'g', ل: 'l', م: 'm', ن: 'n', و: 'v',
  ه: 'h', ة: 'h', ی: 'i', ئ: 'y', ء: '', ؤ: 'v', 'ٔ': '', 'ً': '', '‌': '-',
};
export const slugify = (s: string) =>
  KNOWN[s] ||
  [...s.toLowerCase().replace(/[۰-۹]/g, (d) => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(d)))]
    .map((ch) => (ch in TR ? TR[ch] : ch))
    .join('')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/ß/g, 'ss')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'topic';
export const CATEGORY_SLUG = slugify;

export async function tagIndex() {
  const posts = await getPosts();
  const map = new Map<string, { tag: string; posts: Post[] }>();
  for (const p of posts) for (const t of p.data.tags) {
    const k = slugify(t);
    if (!map.has(k)) map.set(k, { tag: t, posts: [] });
    map.get(k)!.posts.push(p);
  }
  return map;
}
/** Tag pages exist only when at least two posts share the tag (avoid thin pages). */
export const MIN_TAG_POSTS = 2;

export const lastModified = (p: Post) => (p.data.updated || p.data.date).toISOString().slice(0, 10);

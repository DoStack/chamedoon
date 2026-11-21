// Every indexable URL with its true last-modified date (sitemaps, llms.txt, IndexNow).
import { CITIES, VIDEOS } from './site';
import { typesWithData, updatedDay } from './stats';
import { getPosts, postPath, slugify, tagIndex, MIN_TAG_POSTS, lastModified, coverFor } from './blog';

export const LAUNCH = '2026-11-20'; // static pages: content written on this date; bump when their copy changes

export interface Entry { path: string; lastmod: string; group: 'pages' | 'cities' | 'blog' | 'video' | 'data'; title?: string; images?: string[]; video?: (typeof VIDEOS)[number]; alt?: { fa?: string; en?: string } }

export async function allEntries(): Promise<Entry[]> {
  const e: Entry[] = [];
  const pair = (fa: string, en: string | null, lastmod: string, group: Entry['group']) => {
    const alt = { fa, ...(en ? { en } : {}) };
    e.push({ path: fa, lastmod, group, alt });
    if (en) e.push({ path: en, lastmod, group, alt });
  };
  pair('/fa/', '/en/', updatedDay, 'pages');
  pair('/fa/khane/', '/en/rentals/', updatedDay, 'pages');
  pair('/fa/barbari/', '/en/courier/', updatedDay, 'pages');
  pair('/fa/methodology/', '/en/methodology/', updatedDay, 'data');
  pair('/fa/channels/', '/en/channels/', LAUNCH, 'pages');
  pair('/fa/faq/', '/en/faq/', LAUNCH, 'pages');
  pair('/fa/about/', '/en/about/', updatedDay, 'pages');
  pair('/fa/privacy/', null, LAUNCH, 'pages');
  pair('/fa/terms/', null, LAUNCH, 'pages');
  pair('/fa/authors/tahririye/', null, LAUNCH, 'pages');
  for (const c of CITIES) {
    pair(`/fa/${c.slug}/`, `/en/${c.slug}/`, updatedDay, 'cities');
    pair(`/fa/${c.slug}/rent/`, `/en/${c.slug}/rent/`, updatedDay, 'data');
    for (const t of typesWithData(c.slug)) pair(`/fa/${c.slug}/rent/${t}/`, null, updatedDay, 'data');
  }
  const reports = Object.keys(import.meta.glob('../data/reports/*.json')).map((k) => k.match(/(\d{4}-\d{2})\.json$/)![1]);
  for (const m of reports) pair(`/fa/reports/${m}/`, null, m === updatedDay.slice(0, 7) ? updatedDay : `${m}-28`, 'data');
  pair('/fa/videos/', null, LAUNCH, 'video');
  for (const v of VIDEOS) e.push({ path: `/fa/videos/${v.slug}/`, lastmod: v.uploadDate, group: 'video', video: v, title: v.title_fa });
  const posts = await getPosts();
  const newest = posts.length ? posts.map(lastModified).sort().at(-1)! : LAUNCH;
  pair('/fa/blog/', null, newest, 'blog');
  for (const p of posts) {
    const c = coverFor(p);
    e.push({ path: postPath(p), lastmod: lastModified(p), group: 'blog', title: p.data.title, images: [c.jpg, c.og].filter(Boolean) as string[] });
  }
  for (const cat of new Set(posts.map((p) => p.data.category))) {
    const ps = posts.filter((p) => p.data.category === cat);
    pair(`/fa/blog/category/${slugify(cat)}/`, null, ps.map(lastModified).sort().at(-1)!, 'blog');
  }
  for (const [k, v] of await tagIndex()) if (v.posts.length >= MIN_TAG_POSTS) pair(`/fa/blog/tag/${k}/`, null, v.posts.map(lastModified).sort().at(-1)!, 'blog');
  return e;
}

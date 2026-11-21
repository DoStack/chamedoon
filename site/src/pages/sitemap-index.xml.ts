import type { APIRoute } from 'astro';
import { allEntries } from '../lib/routes';
import { abs } from '../lib/site';
export const GET: APIRoute = async () => {
  const e = await allEntries();
  const groups = ['pages', 'cities', 'data', 'blog', 'video'] as const;
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${groups.map((g) => { const last = e.filter((x) => x.group === g).map((x) => x.lastmod).sort().at(-1); return `  <sitemap><loc>${abs(`/sitemap-${g}.xml`)}</loc>${last ? `<lastmod>${last}</lastmod>` : ''}</sitemap>`; }).join('\n')}
</sitemapindex>
`;
  return new Response(body, { headers: { 'content-type': 'application/xml; charset=utf-8' } });
};

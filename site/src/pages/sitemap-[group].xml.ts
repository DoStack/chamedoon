import type { APIRoute } from 'astro';
import { allEntries } from '../lib/routes';
import { abs, MEDIA_BASE } from '../lib/site';
export const getStaticPaths = () => ['pages', 'cities', 'data', 'blog', 'video'].map((group) => ({ params: { group } }));
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
export const GET: APIRoute = async ({ params }) => {
  const e = (await allEntries()).filter((x) => x.group === params.group);
  const urls = e.map((x) => {
    const alts = x.alt && x.alt.en ? [`<xhtml:link rel="alternate" hreflang="fa" href="${abs(x.alt.fa!)}"/>`, `<xhtml:link rel="alternate" hreflang="en" href="${abs(x.alt.en)}"/>`, `<xhtml:link rel="alternate" hreflang="x-default" href="${abs(x.alt.fa!)}"/>`] : [];
    const imgs = (x.images || []).map((i) => `<image:image><image:loc>${abs(i)}</image:loc></image:image>`);
    const vid = x.video ? [`<video:video><video:thumbnail_loc>${abs(x.video.poster)}</video:thumbnail_loc><video:title>${esc(x.video.title_fa)}</video:title><video:description>${esc(x.video.description_fa)}</video:description><video:content_loc>${abs(`${MEDIA_BASE}/videos/${x.video.file}`)}</video:content_loc><video:duration>${x.video.duration}</video:duration><video:publication_date>${x.video.uploadDate}T12:00:00+00:00</video:publication_date><video:family_friendly>yes</video:family_friendly></video:video>`] : [];
    return `  <url><loc>${abs(x.path)}</loc><lastmod>${x.lastmod}</lastmod>${[...alts, ...imgs, ...vid].join('')}</url>`;
  });
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml" xmlns:image="http://www.google.com/schemas/sitemap-image/1.1" xmlns:video="http://www.google.com/schemas/sitemap-video/1.1">
${urls.join('\n')}
</urlset>
`;
  return new Response(body, { headers: { 'content-type': 'application/xml; charset=utf-8' } });
};

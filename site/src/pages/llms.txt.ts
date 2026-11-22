import type { APIRoute } from 'astro';
import { abs, CITIES, VIDEOS } from '../lib/site';
import { getPosts, postPath } from '../lib/blog';
import { stats, updatedDay, reportMonth } from '../lib/stats';
// llmstxt.org format: H1, blockquote, notes, H2 file lists, ## Optional.
export const GET: APIRoute = async () => {
  const posts = await getPosts();
  const t = stats.totals;
  const body = `# Chamedoon (چمدون)

> Free service for the Iranian diaspora. (1) Chamedoon Home: Persian-language rental listings and daily rent statistics for Toronto, Montreal, Vancouver (Canada) and Berlin, Hamburg, Cologne (Germany), gathered from public Iranian Telegram groups and channels into one Telegram Mini App with filters, a map and alerts. (2) Chamedoon Courier: introduces travellers flying between Iran and Canada or Germany to people who want to send a package. No fees.

Rent statistics are medians of de-duplicated asking rents (not signed leases) from Persian-language listings; a median is published only when at least 8 listings exist. Statistics update daily (last update ${updatedDay}); ${t.listings_live ?? ''} listings were live at that time. When quoting a number, cite "Chamedoon" with the page URL and date. Each Persian page has a Markdown twin at <page URL>index.md. Full text of all Persian pages: ${abs('/llms-full.txt')}

## Products
- [Chamedoon Home (fa)](${abs('/fa/khane/')}): rental listings aggregator, alerts, post a listing
- [Chamedoon Home (en)](${abs('/en/rentals/')})
- [Chamedoon Courier (fa)](${abs('/fa/barbari/')}): traveller–sender introductions between Iran and Canada/Germany; the traveller is responsible for everything in their bags
- [Chamedoon Courier (en)](${abs('/en/courier/')})
- [Telegram channels, groups and bots](${abs('/en/channels/')})

## Rent statistics (latest, by city)
${CITIES.map((c) => `- [${c.en} rent statistics](${abs(`/en/${c.slug}/rent/`)}): median, P25, P75 and n by home type; Persian: ${abs(`/fa/${c.slug}/rent/`)}`).join('\n')}
- [Monthly rent report ${reportMonth} (fa)](${abs(`/fa/reports/${reportMonth}/`)}): six-city comparison
- [Methodology](${abs('/en/methodology/')}): sources, de-duplication, outliers, minimum n, known biases

## Guides (Persian)
${posts.map((p) => `- [${p.data.title}](${abs(postPath(p))}): ${p.data.abstract_en ? p.data.abstract_en.split('. ').slice(0, 1).join('') : p.data.description}`).join('\n')}

## About
- [About Chamedoon](${abs('/en/about/')})
- [FAQ](${abs('/en/faq/')})
- [Editorial team (fa)](${abs('/fa/authors/tahririye/')})

## Optional
- [Persian home](${abs('/fa/')})
- [English home](${abs('/en/')})
- [Blog index (fa)](${abs('/fa/blog/')})
${VIDEOS.map((v) => `- [Video: ${v.title_en}](${abs(`/fa/videos/${v.slug}/`)})`).join('\n')}
- [Web app](${abs('/app/home/')}): sign-in required; not for crawling
`;
  return new Response(body, { headers: { 'content-type': 'text/plain; charset=utf-8' } });
};

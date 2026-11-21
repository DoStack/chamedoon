import type { APIRoute } from 'astro';
import { CITIES, abs } from '../../lib/site';
import { MIN_N, type Stats } from '../../lib/stats';
// Monthly rows from the frozen report snapshots (one per month). CC BY 4.0.
const snaps = import.meta.glob<{ default: Stats }>('../../data/reports/*.json', { eager: true });
export const getStaticPaths = () => [...CITIES.map((c) => ({ params: { file: `${c.slug}-rent-monthly` } })), { params: { file: 'all-cities-rent-monthly' } }];
export const GET: APIRoute = ({ params }) => {
  const slug = params.file!.replace(/-rent-monthly$/, '');
  const head = 'month,city,country,currency,home_type,n,median,p25,p75,window,updated,source_url,licence';
  const rows: string[] = [head];
  for (const [k, mod] of Object.entries(snaps).sort()) {
    const month = k.match(/(\d{4}-\d{2})\.json$/)![1];
    const s = mod.default;
    for (const c of s.cities.filter((x) => slug === 'all-cities' || x.slug === slug)) {
      for (const m of c.medians_30d) {
        const ok = m.n >= MIN_N && m.median != null;
        rows.push([month, c.name_en, c.country, c.currency, m.type, m.n, ok ? m.median : '', ok ? m.p25 ?? '' : '', ok ? m.p75 ?? '' : '', '30d', s.generated_at.slice(0, 10), abs(`/en/${c.slug}/rent/`), 'CC BY 4.0 (Chamedoon)'].join(','));
      }
    }
  }
  return new Response(rows.join('\n') + '\n', { headers: { 'content-type': 'text/csv; charset=utf-8' } });
};

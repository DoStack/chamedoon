import { SITE_URL, abs, SURFACES, tme, CITIES, type Lang } from './site';

export const ORG_ID = `${SITE_URL}/#org`;
export const WEBSITE_ID = `${SITE_URL}/#website`;
export const AUTHOR_ID = `${SITE_URL}/fa/authors/tahririye/#org-author`;
export const LOGO = abs('/media/site/img/logo-512.png');

export const organization = () => ({
  '@type': 'Organization',
  '@id': ORG_ID,
  name: 'Chamedoon',
  alternateName: ['چمدون', 'Chamedoon (چمدون)'],
  url: abs('/fa/'),
  logo: { '@type': 'ImageObject', url: LOGO, width: 512, height: 512 },
  description:
    'Free service for the Iranian diaspora: Persian rental listings and rent statistics for Toronto, Montreal, Vancouver, Berlin, Hamburg and Cologne, plus a peer-to-peer luggage courier matcher between Iran and Canada or Germany.',
  foundingDate: '2026',
  areaServed: CITIES.map((c) => ({ '@type': 'City', name: c.en, sameAs: `https://www.wikidata.org/wiki/${c.wikidata}` })),
  knowsLanguage: ['fa', 'en'],
  sameAs: SURFACES.filter((s) => s.kind !== 'group').map((s) => tme(s.username)),
});

export const website = () => ({
  '@type': 'WebSite',
  '@id': WEBSITE_ID,
  url: abs('/fa/'),
  name: 'چمدون',
  alternateName: 'Chamedoon',
  inLanguage: ['fa', 'en'],
  publisher: { '@id': ORG_ID },
});

export interface Crumb { name: string; path: string }
export const breadcrumbs = (items: Crumb[]) => ({
  '@type': 'BreadcrumbList',
  itemListElement: items.map((c, i) => ({ '@type': 'ListItem', position: i + 1, name: c.name, item: abs(c.path) })),
});

export const graph = (...nodes: object[]) => ({ '@context': 'https://schema.org', '@graph': nodes.filter(Boolean) });

export const faqPage = (faq: { q: string; a: string }[], lang: Lang) => ({
  '@type': 'FAQPage',
  inLanguage: lang,
  mainEntity: faq.map((f) => ({ '@type': 'Question', name: f.q, acceptedAnswer: { '@type': 'Answer', text: f.a } })),
});

export const dataset = (opts: { path: string; city?: { en: string; fa: string; wikidata: string; currency: string; slug: string }; name: string; alternateName?: string; description: string; modified: string; csv: string; lang?: 'fa' | 'en' }) => ({
  '@type': 'Dataset',
  '@id': abs(opts.path) + '#dataset',
  name: opts.name,
  alternateName: opts.alternateName,
  description: opts.description,
  url: abs(opts.path),
  creator: { '@id': ORG_ID },
  publisher: { '@id': ORG_ID },
  license: 'https://creativecommons.org/licenses/by/4.0/',
  isAccessibleForFree: true,
  inLanguage: ['fa', 'en'],
  temporalCoverage: '2026-10-01/..',
  dateModified: opts.modified,
  keywords: ['rent', 'Iranian diaspora', 'Persian', 'housing', 'اجاره', 'آمار اجاره'],
  ...(opts.city ? { spatialCoverage: { '@type': 'City', name: opts.city.en, sameAs: `https://www.wikidata.org/wiki/${opts.city.wikidata}` } } : { spatialCoverage: ['Toronto', 'Montreal', 'Vancouver', 'Berlin', 'Hamburg', 'Cologne'].map((n) => ({ '@type': 'City', name: n })) }),
  variableMeasured: ['median asking rent per month', 'first and third quartile of asking rent', 'number of listings (n)', 'home type'],
  measurementTechnique: 'Median of de-duplicated asking rents from Persian-language listings; p5–p95 trim when n ≥ 20; no median when n < 8.',
  distribution: [{ '@type': 'DataDownload', encodingFormat: 'text/csv', contentUrl: abs(opts.csv) }],
});

export const webPage = (path: string, name: string, description: string, lang: Lang, extra: object = {}) => ({
  '@type': 'WebPage',
  '@id': abs(path) + '#page',
  url: abs(path),
  name,
  description,
  inLanguage: lang,
  isPartOf: { '@id': WEBSITE_ID },
  publisher: { '@id': ORG_ID },
  ...extra,
});

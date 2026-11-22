// The site's host, in one place. Moving to another domain = set SITE_URL (and API_BASE if the API moves too)
// in the build env (`.env.production.local`, the nightly vps-rebuild.sh, CI), then follow
// projects/chamedoon/web/DOMAIN-MOVE.md. Everything host-specific (canonicals, sitemaps, JSON-LD, llms.txt,
// Markdown twins, OG images, IndexNow, absolute links inside posts) reads these values.
const clean = (u) => String(u).trim().replace(/\/$/, '');
export const LEGACY_HOSTS = ['https://chamedoon.bigde.li'];
export const SITE_URL = clean(process.env.SITE_URL || 'https://chamedoon.bigde.li');
export const API_BASE = clean(process.env.API_BASE || SITE_URL);
export const SITE_HOST = new URL(SITE_URL).host;

import type { APIRoute } from 'astro';
import { abs } from '../lib/site';
// research/04 §10.1 + this host's app paths (D4, D17). One group per crawler set; named groups repeat the rules.
const rules = `Allow: /
Disallow: /app/
Disallow: /api/
Disallow: /admin/
Disallow: /browse/
Disallow: /auth/
Disallow: /account/
Disallow: /ingest/
Disallow: /search
Disallow: /*?*sort=
Disallow: /*?*price=
Disallow: /*?*min=
Disallow: /*?*max=
Disallow: /*?*rooms=
Disallow: /*?*furnished=
Disallow: /*?*q=
Disallow: /*?*utm_`;
export const GET: APIRoute = () => new Response(`# robots.txt — Chamedoon (چمدون)
# Maximum search and AI visibility. Only the app, API, admin, legacy browse and filter URLs are excluded.

User-agent: *
${rules}

# Search indexes that feed AI answers, user-triggered fetchers and model-training crawlers: all welcome.
User-agent: Googlebot
User-agent: Bingbot
User-agent: OAI-SearchBot
User-agent: Claude-SearchBot
User-agent: PerplexityBot
User-agent: Applebot
User-agent: meta-webindexer
User-agent: DuckAssistBot
User-agent: Amzn-SearchBot
User-agent: MistralAI-Index
User-agent: YouBot
User-agent: ChatGPT-User
User-agent: Claude-User
User-agent: Perplexity-User
User-agent: MistralAI-User
User-agent: GPTBot
User-agent: ClaudeBot
User-agent: CCBot
User-agent: Google-Extended
User-agent: Applebot-Extended
User-agent: meta-externalagent
User-agent: Amazonbot
User-agent: MistralAI-Training
${rules}

Sitemap: ${abs('/sitemap-index.xml')}
`, { headers: { 'content-type': 'text/plain; charset=utf-8' } });

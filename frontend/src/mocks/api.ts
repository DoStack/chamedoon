// A stand-in for the backend, answering by the contract. x-served: false operations (contact, feed, snapshots) work here
// before their backend PRs do. The Playwright tests and the Preview's `?tgapi=mock` both answer from this.
import PARSE from '../../../backend/aisearch/testdata/parse_fixtures.json' with { type: 'json' }
import { ACCOUNT, ALERTS, ALERT_SAVE, CITIES, CONTACT, DEEPLINK_LANDING, EXCHANGE, META, META_P3, NOTIFICATIONS, SNAPSHOT_CARDS, TORONTO, TORONTO_AREAS, listing, manifest, snapshot } from './fixtures'

export type MockResponse = { status: number; body: unknown; headers?: Record<string, string>; html?: boolean }
export type Overrides = Record<string, MockResponse | 'abort' | 'hang'>

const ok = (body: unknown): MockResponse => ({ status: 200, body })
export const coded = (status: number, code: string, extra: object = {}): MockResponse => ({ status, body: { code, detail: 'log only', ...extra } })

const RECENT_ROW = { text: PARSE.parsed.request.text, spec: PARSE.parsed.response.spec, area_labels: PARSE.parsed.response.area_labels }

function allCards() {
  return Object.values(SNAPSHOT_CARDS).flat()
}

// P3, as the server answers once its flags are on. The mock's meta keeps them off unless a test or `?tgp3=1` turns them on.
function answerP3(method: string, pathname: string): MockResponse | null {
  if (pathname === '/api/v1/me/account/') return ok(ACCOUNT)
  if (pathname === '/api/v1/me/notifications/') return ok(NOTIFICATIONS)
  if (pathname === '/api/v1/me/notifications/read/') return ok({ unread: 0 })
  if (pathname === '/api/v1/me/alerts/') return method === 'POST' ? { status: 201, body: ALERT_SAVE } : ok(ALERTS)
  const alert = pathname.match(/^\/api\/v1\/me\/alerts\/(\d+)\/(listings\/|viewed\/)?$/)
  if (!alert) return null
  if (alert[2] === 'viewed/') return { status: 204, body: null }
  if (alert[2] === 'listings/') return ok({ results: TORONTO.slice(0, 3), total: 3, next_cursor: null })
  if (method === 'DELETE') return { status: 204, body: null }
  const row = ALERTS.results.find((item) => item.id === Number(alert[1]))
  return row ? ok(row) : coded(404, 'alert_gone')
}

export function answer(method: string, pathname: string): MockResponse {
  const p3 = answerP3(method, pathname)
  if (p3) return p3
  if (pathname === '/api/v1/public/meta/') return ok(META)
  if (pathname === '/api/v1/auth/exchange/' && method === 'POST') return ok(EXCHANGE)
  if (pathname === '/api/v1/public/cities/') return ok(CITIES)
  if (pathname === '/api/v1/public/deeplink/') return ok(DEEPLINK_LANDING)
  if (pathname === '/api/v1/public/areas/') return ok(TORONTO_AREAS)
  if (pathname === '/feeds/manifest.json') return ok(manifest())
  const file = pathname.match(/^\/feeds\/([a-z]+)\.[a-z0-9]+\.json$/)
  if (file) return ok(snapshot(file[1]))
  if (pathname === '/api/v1/public/feed/') return ok({ generated_at: new Date().toISOString(), results: [], next_cursor: null })
  if (pathname === '/api/v1/me/prefs/') return ok(EXCHANGE.prefs)
  if (pathname === '/api/v1/me/waitlist/') return { status: 201, body: { created: true, city_name: 'Ottawa' } }
  if (pathname === '/api/v1/me/search/parse/') return ok(PARSE.parsed.response)
  if (pathname === '/api/v1/me/search/recent/') return ok(method === 'DELETE' ? { cleared_at: '2026-11-03T12:00:00Z' } : { results: [RECENT_ROW] })
  if (pathname === '/api/v1/me/search/recent/restore/') return ok({ results: [RECENT_ROW] })
  if (pathname === '/api/v1/me/saved/') return ok({ results: [], total: 0, next_cursor: null })
  if (pathname === '/api/v1/me/saved/ids/') return ok({ listing_ids: [] })
  const saved = pathname.match(/^\/api\/v1\/me\/saved\/(\d+)\/$/)
  if (saved) {
    if (method === 'DELETE') return { status: 204, body: null }
    const card = allCards().find((item) => item.id === Number(saved[1])) ?? null
    return ok({ listing_id: Number(saved[1]), state: 'active', card, closed_reason: null, note: '', saved_at: new Date().toISOString() })
  }
  const contact = pathname.match(/^\/api\/v1\/me\/listings\/(\d+)\/contact\/$/)
  if (contact) return ok(CONTACT)
  if (/^\/api\/v1\/me\/listings\/\d+\/availability\/$/.test(pathname)) return ok({ outcome: 'noted' })
  const viewer = pathname.match(/^\/api\/v1\/me\/listings\/(\d+)\/$/)
  const detail = pathname.match(/^\/api\/v1\/public\/listings\/(\d+)\/$/)
  const id = Number((viewer ?? detail)?.[1])
  if (id) {
    const found = allCards().find((card) => card.id === id)
    if (!found) return coded(404, 'listing_gone')
    return viewer ? ok({ is_owner: false, review_state: 'live', listing: listing(found) }) : ok(listing(found))
  }
  return coded(404, 'not_found')
}

export function installMockApi() {
  const real = window.fetch.bind(window)
  const p3 = new URLSearchParams(location.search).has('tgp3')
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.origin)
    if (!url.pathname.startsWith('/api/v1/') && !url.pathname.startsWith('/feeds/')) return real(input, init)
    const { status, body, headers } = p3 && url.pathname === '/api/v1/public/meta/' ? ok(META_P3) : answer(init?.method ?? 'GET', url.pathname)
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
  }
}

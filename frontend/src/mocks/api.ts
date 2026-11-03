// A stand-in for the backend, answering by the contract. x-served: false operations (contact, feed, snapshots) work here
// before their backend PRs do. The Playwright tests and the Preview's `?tgapi=mock` both answer from this.
import { CITIES, CONTACT, DEEPLINK_LANDING, EXCHANGE, META, SNAPSHOT_CARDS, listing, manifest, snapshot } from './fixtures'

export type MockResponse = { status: number; body: unknown; headers?: Record<string, string>; html?: boolean }
export type Overrides = Record<string, MockResponse | 'abort' | 'hang'>

const ok = (body: unknown): MockResponse => ({ status: 200, body })
export const coded = (status: number, code: string, extra: object = {}): MockResponse => ({ status, body: { code, detail: 'log only', ...extra } })

function allCards() {
  return Object.values(SNAPSHOT_CARDS).flat()
}

export function answer(method: string, pathname: string): MockResponse {
  if (pathname === '/api/v1/public/meta/') return ok(META)
  if (pathname === '/api/v1/auth/exchange/' && method === 'POST') return ok(EXCHANGE)
  if (pathname === '/api/v1/public/cities/') return ok(CITIES)
  if (pathname === '/api/v1/public/deeplink/') return ok(DEEPLINK_LANDING)
  if (pathname === '/feeds/manifest.json') return ok(manifest())
  const file = pathname.match(/^\/feeds\/([a-z]+)\.[a-z0-9]+\.json$/)
  if (file) return ok(snapshot(file[1]))
  if (pathname === '/api/v1/public/feed/') return ok({ generated_at: new Date().toISOString(), results: [], next_cursor: null })
  if (pathname === '/api/v1/me/prefs/') return ok(EXCHANGE.prefs)
  if (pathname === '/api/v1/me/waitlist/') return { status: 201, body: { created: true, city_name: 'Ottawa' } }
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
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.origin)
    if (!url.pathname.startsWith('/api/v1/') && !url.pathname.startsWith('/feeds/')) return real(input, init)
    const { status, body, headers } = answer(init?.method ?? 'GET', url.pathname)
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
  }
}

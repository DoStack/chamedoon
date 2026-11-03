// A stand-in for /api/v1/courier/, answering from fixtures a backend test holds to the serializers
// (backend/tests/test_courier_fixtures.py). The Playwright tests and the Preview's `?tgapi=mock` both answer from this.
import fixtures from './courier-fixtures.json' with { type: 'json' }

export type MockResponse = { status: number; body: unknown; headers?: Record<string, string>; html?: boolean }

type Fixtures = typeof fixtures

export const fx = <K extends keyof Fixtures>(name: K): Fixtures[K]['body'] => structuredClone(fixtures[name].body)

const ok = (body: unknown, status = 200): MockResponse => ({ status, body })
export const coded = (status: number, code: string, extra: object = {}): MockResponse => ({ status, body: { code, detail: 'log only', ...extra } })

// The server's keys are this month and the next two, so the sample follows the calendar.
function facets() {
  const now = new Date()
  const months = [3, 12, 5].map((count, step) => {
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + step, 1))
    return { key: `${day.getUTCFullYear()}-${String(day.getUTCMonth() + 1).padStart(2, '0')}`, count }
  })
  return { ...fx('facets'), months }
}

export function answer(method: string, pathname: string, search = ''): MockResponse {
  const path = pathname.replace('/api/v1/courier/', '')
  if (method === 'PATCH' && /^me\/alerts\/\d+\/$/.test(path)) return ok(fx('alertSaved'))
  if (method === 'GET' && /^me\/requests\/\d+\/$/.test(path)) return ok(fx('mineActive'))
  if (method === 'GET' && /^me\/requests\/\d+\/counterparts\/$/.test(path)) return ok(fx('counterparts'))
  if (method === 'GET' && /^requests\/\d+\/$/.test(path)) return ok(fx('requestDetail'))
  if (method === 'GET' && /^requests\/\d+\/published\/$/.test(path)) return ok(fx('published'))
  if (method === 'POST' && /^requests\/\d+\/intro\/$/.test(path)) return ok(fx('introTarget'), 201)
  if (method === 'GET' && /^me\/intros\/\d+\/$/.test(path)) return ok(fx('introTarget'))
  if (method === 'POST' && /^me\/intros\/\d+\/respond\/$/.test(path)) return ok(fx('introAccepted'))
  if (method === 'GET' && /^me\/matches\/\d+\/$/.test(path)) return ok(fx('match'))
  switch (`${method} ${path}`) {
    case 'GET meta/':
      return ok(fx('meta'))
    case 'POST auth/exchange/':
      return ok(fx('exchange'))
    case 'GET me/':
      return ok(fx('me'))
    case 'GET guide/':
      return ok(fx('guide'))
    case 'PATCH me/prefs/':
      return ok(fx('me').prefs)
    case 'POST me/requests/':
      return ok(fx('mineActive'), 201)
    case 'GET me/requests/':
      return ok({ items: [fx('mineActive')] })
    case 'GET me/matches/':
      return ok(new URLSearchParams(search).get('state') === 'archive' ? { incoming: [], outgoing: [], accepted: [], closed: [], matches: [] } : fx('inbox'))
    case 'GET locations/':
      return ok(fx('locations'))
    case 'POST cities/':
      return ok(fx('city'))
    case 'POST deeplink/':
      return ok(fx('deeplink'))
    case 'GET requests/':
      return ok(new URLSearchParams(search).get('side') === 'SUPPLY' ? fx('packages') : fx('travelers'))
    case 'GET requests/facets/':
      return ok(facets())
    case 'GET me/alerts/':
      return ok(fx('alerts'))
    case 'POST me/alerts/':
      return ok(fx('alertSaved'), 201)
  }
  return coded(404, 'not_found')
}

export function installMockApi() {
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.origin)
    if (!url.pathname.startsWith('/api/v1/courier/')) return real(input, init)
    const { status, body, headers } = answer(init?.method ?? 'GET', url.pathname, url.search)
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
  }
}

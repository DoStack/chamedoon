// A stand-in for /api/v1/courier/, answering from fixtures a backend test holds to the serializers
// (backend/tests/test_courier_fixtures.py). The Playwright tests and the Preview's `?tgapi=mock` both answer from this.
import fixtures from './courier-fixtures.json' with { type: 'json' }

export type MockResponse = { status: number; body: unknown; headers?: Record<string, string>; html?: boolean }

type Fixtures = typeof fixtures

export const fx = <K extends keyof Fixtures>(name: K): Fixtures[K]['body'] => structuredClone(fixtures[name].body)

const ok = (body: unknown, status = 200): MockResponse => ({ status, body })
export const coded = (status: number, code: string, extra: object = {}): MockResponse => ({ status, body: { code, detail: 'log only', ...extra } })

export function answer(method: string, pathname: string): MockResponse {
  const path = pathname.replace('/api/v1/courier/', '')
  switch (`${method} ${path}`) {
    case 'GET meta/':
      return ok(fx('meta'))
    case 'POST auth/exchange/':
      return ok(fx('exchange'))
    case 'GET me/':
      return ok(fx('me'))
    case 'GET guide/':
      return ok(fx('guide'))
  }
  return coded(404, 'not_found')
}

export function installMockApi() {
  const real = window.fetch.bind(window)
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, location.origin)
    if (!url.pathname.startsWith('/api/v1/courier/')) return real(input, init)
    const { status, body, headers } = answer(init?.method ?? 'GET', url.pathname)
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
  }
}

import type { Page } from '@playwright/test'

export type Forced =
  | { kind: 'network_error' }
  | { kind: 'offline' } // call goOfflineIfForced after the page has loaded
  | { kind: 'timeout' } // the response never comes; advance page.clock past the client timeout
  | { kind: 'html_error'; status: number } // a Vercel platform error page
  | { kind: 'not_json' } // 200 with HTML, as a login redirect would give
  | { kind: 'code'; status: number; code: string; detail?: string; retryAfter?: number }
  | { kind: 'ok'; body: unknown }

// Forces one outcome on every request matching `url`, for any flow's tests.
export async function forceOutcome(page: Page, url: string, forced: Forced) {
  if (forced.kind === 'offline') return
  await page.route(url, async (route) => {
    switch (forced.kind) {
      case 'network_error':
        return route.abort('failed')
      case 'timeout':
        return // Left unanswered on purpose.
      case 'html_error':
        return route.fulfill({ status: forced.status, contentType: 'text/html', body: '<html>FUNCTION_INVOCATION_FAILED</html>' })
      case 'not_json':
        return route.fulfill({ status: 200, contentType: 'text/html', body: '<html>sign in</html>' })
      case 'code':
        return route.fulfill({
          status: forced.status,
          contentType: 'application/json',
          headers: forced.retryAfter ? { 'Retry-After': String(forced.retryAfter) } : {},
          body: JSON.stringify({ code: forced.code, detail: forced.detail ?? 'log only' }),
        })
      case 'ok':
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(forced.body) })
    }
  })
}

export async function goOfflineIfForced(page: Page, forced: Forced) {
  if (forced.kind === 'offline') await page.context().setOffline(true)
}

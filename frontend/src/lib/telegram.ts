type Inset = { top: number; bottom: number; left: number; right: number }

export type WebApp = {
  platform?: string
  version?: string
  initData?: string
  viewportStableHeight?: number
  safeAreaInset?: Inset
  contentSafeAreaInset?: Inset
  ready?: () => void
  expand?: () => void
  onEvent?: (name: string, fn: () => void) => void
}

declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp }
  }
}

const NO_INSET: Inset = { top: 0, bottom: 0, left: 0, right: 0 }

const MOCKS: Record<string, { safe: Inset; content: Inset; h: number; stable: number }> = {
  ios: { safe: { top: 47, bottom: 34, left: 0, right: 0 }, content: { top: 46, bottom: 0, left: 0, right: 0 }, h: 720, stable: 700 },
  android: { safe: { top: 24, bottom: 16, left: 0, right: 0 }, content: { top: 48, bottom: 0, left: 0, right: 0 }, h: 680, stable: 660 },
  desktop: { safe: NO_INSET, content: NO_INSET, h: 600, stable: 600 },
}

// `?tgmock=ios|android|desktop`, and `&tginit=<value>` for initData, for tests. __TG_MOCK__ is false in the Production build, which drops this code.
function installMock(platform: string) {
  const m = MOCKS[platform] ?? MOCKS.ios
  const handlers: Record<string, Array<() => void>> = {}
  window.Telegram = {
    WebApp: {
      platform,
      version: '8.0',
      initData: new URLSearchParams(location.search).get('tginit') ?? '',
      viewportHeight: m.h,
      viewportStableHeight: m.stable,
      safeAreaInset: m.safe,
      contentSafeAreaInset: m.content,
      ready() {},
      expand() {},
      onEvent(name: string, fn: () => void) {
        ;(handlers[name] ??= []).push(fn)
      },
      __fire(name: string, patch: object) {
        Object.assign(this, patch)
        ;(handlers[name] ?? []).forEach((f) => f())
      },
    } as WebApp,
  }
}

export function getWebApp(): WebApp | undefined {
  return window.Telegram?.WebApp
}

export function initTelegram(): WebApp | undefined {
  if (__TG_MOCK__) {
    const mock = new URLSearchParams(location.search).get('tgmock')
    if (mock) installMock(mock)
  }
  const app = getWebApp()
  // telegram-web-app.js also defines WebApp outside Telegram, with platform "unknown".
  if (!app?.version || !app.platform || app.platform === 'unknown') return undefined

  const root = document.documentElement.style
  const apply = () => {
    const safe = app.safeAreaInset ?? NO_INSET
    const content = app.contentSafeAreaInset ?? NO_INSET
    if (app.viewportStableHeight) root.setProperty('--app-h', `${app.viewportStableHeight}px`)
    root.setProperty('--safe-t', `${safe.top + content.top}px`)
    root.setProperty('--safe-b', `${safe.bottom + content.bottom}px`)
    root.setProperty('--safe-l', `${safe.left + content.left}px`)
    root.setProperty('--safe-r', `${safe.right + content.right}px`)
  }
  app.ready?.()
  app.expand?.()
  apply()
  for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged']) app.onEvent?.(event, apply)
  return app
}

// 44 px targets on phones, 36 px on desktop: see the density rules in index.css.
export function initDensity() {
  const touch = window.matchMedia('(pointer: coarse), (max-width: 767px)')
  const apply = () => {
    document.documentElement.classList.toggle('density-touch', touch.matches)
    document.documentElement.classList.toggle('density-desktop', !touch.matches)
  }
  apply()
  touch.addEventListener('change', apply)
}

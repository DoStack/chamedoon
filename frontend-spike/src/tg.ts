// SPIKE P0: thin Telegram.WebApp wrapper plus a mock for ?tgmock=ios|android|desktop.
type Inset = { top: number; bottom: number; left: number; right: number }

const MOCKS: Record<string, { safe: Inset; content: Inset; h: number; stable: number }> = {
  ios: { safe: { top: 47, bottom: 34, left: 0, right: 0 }, content: { top: 46, bottom: 0, left: 0, right: 0 }, h: 720, stable: 700 },
  android: { safe: { top: 24, bottom: 16, left: 0, right: 0 }, content: { top: 48, bottom: 0, left: 0, right: 0 }, h: 680, stable: 660 },
  desktop: { safe: { top: 0, bottom: 0, left: 0, right: 0 }, content: { top: 0, bottom: 0, left: 0, right: 0 }, h: 600, stable: 600 },
}

function installMock(platform: string) {
  const m = MOCKS[platform] ?? MOCKS.ios
  const handlers: Record<string, Array<() => void>> = {}
  const app = {
    __mock: true,
    platform,
    version: '8.0',
    initData: '',
    colorScheme: 'light',
    themeParams: {},
    isExpanded: true,
    viewportHeight: m.h,
    viewportStableHeight: m.stable,
    safeAreaInset: m.safe,
    contentSafeAreaInset: m.content,
    calls: [] as string[],
    ready() { this.calls.push('ready') },
    expand() { this.calls.push('expand') },
    onEvent(name: string, fn: () => void) { (handlers[name] ??= []).push(fn) },
    offEvent(name: string, fn: () => void) { handlers[name] = (handlers[name] ?? []).filter((f) => f !== fn) },
    __fire(name: string, patch: Record<string, unknown>) {
      Object.assign(this, patch)
      ;(handlers[name] ?? []).forEach((f) => f())
    },
  }
  ;(window as any).Telegram = { WebApp: app }
}

export function initTelegram() {
  const mock = new URLSearchParams(location.search).get('tgmock')
  if (mock) installMock(mock)
  const app = (window as any).Telegram?.WebApp
  const root = document.documentElement.style

  const apply = () => {
    if (!app?.version || !app.platform || app.platform === 'unknown') return
    const safe: Inset = app.safeAreaInset ?? { top: 0, bottom: 0, left: 0, right: 0 }
    const content: Inset = app.contentSafeAreaInset ?? { top: 0, bottom: 0, left: 0, right: 0 }
    if (app.viewportStableHeight) root.setProperty('--app-h', `${app.viewportStableHeight}px`)
    root.setProperty('--safe-t', `${safe.top + content.top}px`)
    root.setProperty('--safe-b', `${safe.bottom + content.bottom}px`)
    root.setProperty('--safe-l', `${safe.left + content.left}px`)
    root.setProperty('--safe-r', `${safe.right + content.right}px`)
  }
  if (app) {
    app.ready?.()
    app.expand?.()
    apply()
    for (const e of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged']) app.onEvent?.(e, apply)
  }
  return app
}

export function tgReport() {
  const app = (window as any).Telegram?.WebApp
  const css = getComputedStyle(document.documentElement)
  const shell = document.querySelector('.app-shell') as HTMLElement | null
  const top = document.querySelector('.app-top') as HTMLElement | null
  const bottom = document.querySelector('.app-bottom') as HTMLElement | null
  return {
    mock: !!app?.__mock,
    platform: app?.platform ?? null,
    version: app?.version ?? null,
    viewportHeight: app?.viewportHeight ?? null,
    viewportStableHeight: app?.viewportStableHeight ?? null,
    isExpanded: app?.isExpanded ?? null,
    safeAreaInset: app?.safeAreaInset ?? null,
    contentSafeAreaInset: app?.contentSafeAreaInset ?? null,
    calls: app?.calls ?? null,
    htmlDir: document.documentElement.dir,
    computedDirection: getComputedStyle(document.body).direction,
    fontFamily: getComputedStyle(document.body).fontFamily.slice(0, 40),
    vazirmatnLoaded: document.fonts.check('16px "Vazirmatn Variable"', 'سلام'),
    vars: { h: css.getPropertyValue('--app-h'), t: css.getPropertyValue('--safe-t'), b: css.getPropertyValue('--safe-b') },
    shellHeight: shell?.getBoundingClientRect().height ?? null,
    topPaddingPx: top ? getComputedStyle(top).paddingTop : null,
    bottomPaddingPx: bottom ? getComputedStyle(bottom).paddingBottom : null,
    innerWidth: window.innerWidth,
    scrollWidthOverflow: document.documentElement.scrollWidth > window.innerWidth,
  }
}

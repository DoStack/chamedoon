import { report } from './report'

type Inset = { top: number; bottom: number; left: number; right: number }

export type WebApp = {
  platform?: string
  version?: string
  initData?: string
  initDataUnsafe?: { start_param?: string; user?: { id?: number; language_code?: string; photo_url?: string } }
  colorScheme?: 'light' | 'dark'
  viewportHeight?: number
  viewportStableHeight?: number
  isFullscreen?: boolean
  safeAreaInset?: Inset
  contentSafeAreaInset?: Inset
  ready?: () => void
  expand?: () => void
  requestFullscreen?: () => void
  close?: () => void
  onEvent?: (name: string, fn: (payload?: { error?: string }) => void) => void
  isVersionAtLeast?: (version: string) => boolean
  openLink?: (url: string) => void
  openTelegramLink?: (url: string) => void
  disableVerticalSwipes?: () => void
  // Bot API 6.9.
  requestWriteAccess?: (callback?: (granted: boolean) => void) => void
  // Bot API 6.9. Telegram sends the contact to the bot; the callback only says whether it was shared.
  requestContact?: (callback?: (shared: boolean) => void) => void
  BackButton?: { show(): void; hide(): void; onClick(fn: () => void): void; offClick(fn: () => void): void }
  // Bot API 8.0.
  shareMessage?: (preparedId: string, callback?: (sent: boolean) => void) => void
  downloadFile?: (params: { url: string; file_name: string }, callback?: (accepted: boolean) => void) => void
  LocationManager?: {
    isLocationAvailable: boolean
    isAccessRequested: boolean
    isAccessGranted: boolean
    init(callback?: () => void): void
    getLocation(callback: (data: { latitude: number; longitude: number } | null) => void): void
    openSettings(): void
  }
  CloudStorage?: {
    getItem(key: string, callback: (error: unknown, value?: string) => void): void
    setItem(key: string, value: string, callback?: (error: unknown, stored?: boolean) => void): void
  }
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
for (const platform of ['tdesktop', 'macos', 'weba', 'webk']) MOCKS[platform] = MOCKS.desktop

// `?tgmock=ios|android|desktop|tdesktop|macos|weba|webk`, with `&tginit=` for initData, `&tgstart=` for the start param,
// `&tgversion=`, `&tgtheme=dark` and `&tgfullscreen=<error>` for a failed fullscreen, for tests. __TG_MOCK__ is false in the Production build, which drops this code.
function installMock(platform: string) {
  const m = MOCKS[platform] ?? MOCKS.ios
  const params = new URLSearchParams(location.search)
  const handlers: Record<string, Array<(payload?: { error?: string }) => void>> = {}
  const calls: string[] = []
  const backs = new Set<() => void>()
  const version = params.get('tgversion') ?? '8.0'
  const opened: string[] = []
  let backVisible = false
  window.Telegram = {
    WebApp: {
      platform,
      version,
      initData: params.get('tginit') ?? '',
      initDataUnsafe: { start_param: params.get('tgstart') ?? undefined, user: { language_code: params.get('tglang') ?? undefined, photo_url: params.get('tgphoto') ?? undefined } },
      colorScheme: params.get('tgtheme') === 'dark' ? 'dark' : 'light',
      viewportHeight: m.h,
      viewportStableHeight: m.stable,
      safeAreaInset: m.safe,
      contentSafeAreaInset: m.content,
      ready() {},
      expand() {
        calls.push('expand')
      },
      requestFullscreen() {
        calls.push('requestFullscreen')
        const error = params.get('tgfullscreen')
        if (error) (handlers.fullscreenFailed ?? []).forEach((f) => f({ error }))
        else (handlers.fullscreenChanged ?? []).forEach((f) => f())
      },
      close() {
        opened.push('close')
      },
      isVersionAtLeast: (wanted: string) => parseFloat(version) >= parseFloat(wanted),
      openLink: (url: string) => void opened.push(url),
      openTelegramLink: (url: string) => void opened.push(url),
      disableVerticalSwipes() {},
      // `&tgshare=no` for a person who closes Telegram's chat picker without sending.
      shareMessage(preparedId: string, callback?: (sent: boolean) => void) {
        calls.push(`shareMessage:${preparedId}`)
        callback?.(params.get('tgshare') !== 'no')
      },
      downloadFile(file: { url: string; file_name: string }, callback?: (accepted: boolean) => void) {
        calls.push(`downloadFile:${file.url}`)
        callback?.(true)
      },
      // `&tgwrite=no` for a person who declines. Telegram draws its own prompt, which the mock skips.
      requestWriteAccess(callback?: (granted: boolean) => void) {
        calls.push('requestWriteAccess')
        callback?.(params.get('tgwrite') !== 'no')
      },
      // `&tgcontact=no` for a person who declines the contact popup.
      requestContact(callback?: (shared: boolean) => void) {
        calls.push('requestContact')
        callback?.(params.get('tgcontact') !== 'no')
      },
      // `&tglocation=denied` for location switched off. Telegram draws its own permission prompt, which the mock skips.
      LocationManager: {
        isLocationAvailable: true,
        isAccessRequested: params.get('tglocation') === 'denied',
        isAccessGranted: params.get('tglocation') !== 'denied',
        init: (callback?: () => void) => callback?.(),
        getLocation: (callback: (data: { latitude: number; longitude: number } | null) => void) => callback(params.get('tglocation') === 'denied' ? null : { latitude: 43.7, longitude: -79.42 }),
        openSettings: () => void calls.push('openLocationSettings'),
      },
      BackButton: {
        show() {
          backVisible = true
        },
        hide() {
          backVisible = false
        },
        onClick: (fn: () => void) => void backs.add(fn),
        offClick: (fn: () => void) => void backs.delete(fn),
      },
      onEvent(name: string, fn: () => void) {
        ;(handlers[name] ??= []).push(fn)
      },
      __fire(name: string, patch: object) {
        Object.assign(this, patch)
        ;(handlers[name] ?? []).forEach((f) => f())
      },
      __back: () => backs.forEach((f) => f()),
      __backVisible: () => backVisible,
      __opened: opened,
      __calls: calls,
    } as WebApp,
  }
}

export function getWebApp(): WebApp | undefined {
  return window.Telegram?.WebApp
}

export function isVersionAtLeast(version: string) {
  return getWebApp()?.isVersionAtLeast?.(version) ?? false
}

export function getStartParam() {
  return getWebApp()?.initDataUnsafe?.start_param ?? ''
}

// The person's own Telegram photo, when their privacy settings let Mini Apps see it. Only https URLs.
export function getUserPhoto() {
  const url = getWebApp()?.initDataUnsafe?.user?.photo_url ?? ''
  return url.startsWith('https://') ? url : ''
}

export type ThemeMode = 'light' | 'dark' | 'auto'

// F22: auto follows Telegram, and is all there is until a person chooses.
let themeMode: ThemeMode = 'auto'

function applyTheme() {
  const dark = themeMode === 'auto' ? getWebApp()?.colorScheme === 'dark' : themeMode === 'dark'
  document.documentElement.classList.toggle('dark', dark)
}

export function setThemeMode(mode: ThemeMode) {
  themeMode = mode
  applyTheme()
}

export function initTelegram(): WebApp | undefined {
  if (__TG_MOCK__) {
    const mock = new URLSearchParams(location.search).get('tgmock')
    if (mock) installMock(mock)
  }
  const app = getWebApp()
  // telegram-web-app.js also defines WebApp outside Telegram, with platform "unknown".
  if (!app?.version || !app.platform || app.platform === 'unknown') return undefined

  const phone = app.platform === 'ios' || app.platform === 'android'
  const root = document.documentElement.style
  const apply = () => {
    const safe = app.safeAreaInset ?? NO_INSET
    const content = app.contentSafeAreaInset ?? NO_INSET
    // A phone keeps the stable height, so the keyboard does not jump the layout. A desktop window has no keyboard, and a
    // taller window shows more. Telegram gives no way to set the window size.
    const height = phone ? app.viewportStableHeight : (app.viewportHeight ?? app.viewportStableHeight)
    if (height) root.setProperty('--app-h', `${height}px`)
    root.setProperty('--safe-t', `${safe.top + content.top}px`)
    root.setProperty('--safe-sys-t', `${safe.top}px`)
    root.setProperty('--safe-bar-t', `${content.top}px`)
    root.setProperty('--safe-b', `${safe.bottom + content.bottom}px`)
    root.setProperty('--safe-l', `${safe.left + content.left}px`)
    root.setProperty('--safe-r', `${safe.right + content.right}px`)
  }
  app.ready?.()
  app.expand?.()
  apply()
  applyTheme()
  for (const event of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged', 'fullscreenChanged']) app.onEvent?.(event, () => apply())
  // Phones open full page, from Bot API 8.0. Failing leaves the expanded view, which is a fine place to be, so the person
  // sees nothing. Unverified on real clients.
  if (phone && isVersionAtLeast('8.0')) {
    app.onEvent?.('fullscreenFailed', (failure) => {
      if (failure?.error !== 'ALREADY_FULLSCREEN') report('fullscreen_failed', { error: failure?.error })
    })
    app.requestFullscreen?.()
  }
  app.onEvent?.('themeChanged', applyTheme)
  // Bot API 7.7. Below it the list has a refresh button instead of pull-to-refresh (F04#13). Unverified on real clients.
  if (isVersionAtLeast('7.7')) app.disableVerticalSwipes?.()
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

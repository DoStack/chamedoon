import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getWebApp, initTelegram, type WebApp } from './telegram'

const style = () => document.documentElement.style

beforeEach(() => {
  document.documentElement.removeAttribute('style')
  window.Telegram = undefined
  window.history.replaceState(null, '', '/')
})

describe('initTelegram', () => {
  it('sums safe area insets and uses the stable height', () => {
    const app: WebApp = {
      platform: 'ios',
      version: '8.0',
      viewportStableHeight: 700,
      safeAreaInset: { top: 47, bottom: 34, left: 1, right: 2 },
      contentSafeAreaInset: { top: 46, bottom: 3, left: 4, right: 5 },
      ready: vi.fn(),
      expand: vi.fn(),
    }
    window.Telegram = { WebApp: app }
    expect(initTelegram()).toBe(app)
    expect(style().getPropertyValue('--app-h')).toBe('700px')
    expect(style().getPropertyValue('--safe-t')).toBe('93px')
    expect(style().getPropertyValue('--safe-b')).toBe('37px')
    expect(style().getPropertyValue('--safe-l')).toBe('5px')
    expect(style().getPropertyValue('--safe-r')).toBe('7px')
    expect(app.ready).toHaveBeenCalledOnce()
    expect(app.expand).toHaveBeenCalledOnce()
  })

  it('reapplies when Telegram reports a change', () => {
    const handlers: Record<string, () => void> = {}
    const app: WebApp = {
      platform: 'android',
      version: '8.0',
      viewportStableHeight: 660,
      onEvent: (name, fn) => {
        handlers[name] = fn
      },
    }
    window.Telegram = { WebApp: app }
    initTelegram()
    app.viewportStableHeight = 500
    handlers.viewportChanged()
    expect(style().getPropertyValue('--app-h')).toBe('500px')
  })

  it('leaves CSS defaults outside Telegram', () => {
    window.Telegram = { WebApp: { platform: 'unknown', version: '6.0' } }
    expect(initTelegram()).toBeUndefined()
    expect(style().getPropertyValue('--app-h')).toBe('')
  })

  it('installs the mock from the query string', () => {
    window.history.replaceState(null, '', '/?tgmock=android')
    initTelegram()
    expect(getWebApp()?.platform).toBe('android')
    expect(style().getPropertyValue('--safe-t')).toBe('72px')
  })
})

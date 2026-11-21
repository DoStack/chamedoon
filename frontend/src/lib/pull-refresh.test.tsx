import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, render } from '@testing-library/react'
import { createRef, type MutableRefObject } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const tracked: Array<[string, Record<string, unknown>]> = []
vi.mock('./report', () => ({ track: (name: string, data: Record<string, unknown>) => void tracked.push([name, data]) }))

const { default: PullRefresh, PULL_THRESHOLD, canPull, findScroller, rubber, surface } = await import('./pull-refresh-impl')

afterEach(() => {
  tracked.length = 0
  delete window.Telegram
  document.body.innerHTML = ''
})

describe('rubber band', () => {
  it('follows the finger, then resists, and never passes its cap', () => {
    expect(rubber(0)).toBe(0)
    expect(rubber(-40)).toBe(0)
    expect(rubber(20)).toBeGreaterThan(10)
    expect(rubber(150)).toBeGreaterThanOrEqual(PULL_THRESHOLD)
    expect(rubber(60)).toBeLessThan(PULL_THRESHOLD)
    expect(rubber(10_000)).toBeLessThanOrEqual(140)
    expect(rubber(300) - rubber(250)).toBeLessThan(rubber(50) - rubber(0))
  })
})

describe('surface and pull support', () => {
  it('is the web without Telegram launch data, where pulling is always on', () => {
    expect(surface()).toBe('web')
    expect(canPull()).toBe(true)
  })

  it('is the Mini App with launch data, and pulls only from Bot API 7.7', () => {
    window.Telegram = { WebApp: { initData: 'signed', isVersionAtLeast: (v: string) => parseFloat('7.0') >= parseFloat(v) } }
    expect(surface()).toBe('miniapp')
    expect(canPull()).toBe(false)
    window.Telegram = { WebApp: { initData: 'signed', isVersionAtLeast: (v: string) => parseFloat('8.0') >= parseFloat(v) } }
    expect(canPull()).toBe(true)
  })
})

describe('findScroller', () => {
  const box = (html: string) => {
    document.body.innerHTML = html
    for (const element of document.querySelectorAll<HTMLElement>('[data-h]')) Object.defineProperty(element, 'clientHeight', { value: Number(element.dataset.h) })
  }

  it('takes the tallest vertical scroller next to the nav, never one in a dialog or the nav', () => {
    box(`<main class="app-main"><div>
      <nav id="nav"><div style="overflow-y:auto" data-h="900"></div></nav>
      <div id="small" style="overflow-y:auto" data-h="100"></div>
      <div id="list" style="overflow-y:auto" data-h="600"></div>
      <div role="dialog"><div style="overflow-y:auto" data-h="800"></div></div>
    </div></main>`)
    expect(findScroller(document.getElementById('nav'))?.id).toBe('list')
  })

  it('falls back to the main area', () => {
    box('<main class="app-main" id="main"><div><nav id="nav"></nav><div style="overflow:hidden" data-h="600"></div></div></main>')
    expect(findScroller(document.getElementById('nav'))?.id).toBe('main')
  })
})

describe('reselect', () => {
  function mount() {
    const client = new QueryClient()
    let calls = 0
    function Screen() {
      useQuery({ queryKey: ['feed'], queryFn: () => ++calls })
      return null
    }
    const api: MutableRefObject<((tab: string) => void) | null> = { current: null }
    const nav = createRef<HTMLElement>()
    render(
      <QueryClientProvider client={client}>
        <Screen />
        <div>
          <nav ref={nav} />
        </div>
        <PullRefresh nav={nav} product="home" screen="search" enabled api={api} />
      </QueryClientProvider>,
    )
    return { api, calls: () => calls }
  }

  it('at the top refreshes the screen once, and reports it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const { api, calls } = mount()
    await act(async () => void (await vi.advanceTimersByTimeAsync(10)))
    expect(calls()).toBe(1)
    act(() => {
      api.current!('search')
      api.current!('search') // In flight: ignored.
    })
    await act(async () => void (await vi.advanceTimersByTimeAsync(600)))
    expect(calls()).toBe(2)
    expect(tracked.filter(([name]) => name === 'nav_tab_reselect')).toHaveLength(2)
    expect(tracked[0]).toEqual(['nav_tab_reselect', { product: 'home', tab: 'search', scrolled_from_px: 0, action: 'refresh', surface: 'web' }])
    const done = tracked.filter(([name]) => name === 'screen_refresh_done')
    expect(done).toHaveLength(1)
    expect(done[0][1]).toMatchObject({ product: 'home', screen: 'search', trigger: 'tab', ok: true })
    vi.useRealTimers()
  })
})

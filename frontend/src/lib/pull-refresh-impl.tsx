import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState, type MutableRefObject, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { track } from './report'
import { getWebApp, isVersionAtLeast } from './telegram'

// Instagram-style: tap the tab you are on to go back to the top and refresh, or pull down at the top to refresh.
// One gesture for every tabbed screen of both apps, owned by the nav, so the screens keep their own scrollers untouched.
export const PULL_THRESHOLD = 64
const PULL_MAX = 140
const HOLD_PX = 52
const MIN_SPIN_MS = 450
// Not inside sheets, dialogs, menus or the map, and never where a screen opts out.
const NO_PULL = '[role="dialog"],[role="menu"],[role="listbox"],[data-vaul-drawer],.maplibregl-map,[data-no-pull]'

export type Product = 'home' | 'courier'
type Trigger = 'tab' | 'pull'

let inFlight = false

export const surface = () => (getWebApp()?.initData ? 'miniapp' : 'web')
// Below Bot API 7.7 a pull-down collapses the Mini App, so only the tab tap refreshes there.
export const canPull = () => !getWebApp()?.initData || isVersionAtLeast('7.7')
const reduced = () => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Rubber band: follows the finger at first, then resists, never past PULL_MAX.
export function rubber(dy: number) {
  return dy <= 0 ? 0 : PULL_MAX * (1 - Math.exp(-dy / 220))
}

const scrolls = (element: Element) => {
  const y = getComputedStyle(element).overflowY
  return (y === 'auto' || y === 'scroll') && (element as HTMLElement).clientHeight > 0
}

// The screen's scroller: the tallest vertical scroller next to the nav, else the app's main area.
export function findScroller(nav: HTMLElement | null): HTMLElement | null {
  const root = nav?.parentElement
  let best: HTMLElement | null = null
  if (root) {
    for (const element of root.querySelectorAll<HTMLElement>('*')) {
      if (nav!.contains(element) || element.closest(NO_PULL) || !scrolls(element)) continue
      if (!best || element.clientHeight > best.clientHeight) best = element
    }
  }
  return best ?? (nav?.closest('.app-main') as HTMLElement | null) ?? null
}

// Refetches what the screen shows: the active queries of its client. `ok` is false when any of them ends in an error.
async function refetchScreen(client: QueryClient) {
  const started = Date.now()
  await Promise.all([client.refetchQueries({ type: 'active' }), new Promise((resolve) => setTimeout(resolve, MIN_SPIN_MS))])
  const ok = client.getQueryCache().findAll({ type: 'active' }).every((query) => query.state.status !== 'error')
  return { ok, duration_ms: Date.now() - started }
}

type Phase = { pull: number; refreshing: boolean; top: number; left: number; width: number }

export type PullProps = { nav: RefObject<HTMLElement | null>; product: Product; screen: string; enabled: boolean; api: MutableRefObject<((tab: string) => void) | null> }

// Loaded after the nav, in its own chunk (see use-pull-refresh.tsx), so first load stays under budget.
export default function PullRefresh({ nav, product, screen, enabled, api }: PullProps) {
  const client = useQueryClient()
  const [phase, setPhase] = useState<Phase | null>(null)
  const live = useRef({ product, screen, client })
  live.current = { product, screen, client }

  const refreshRef = useRef<(trigger: Trigger, scroller: HTMLElement | null) => Promise<void>>(async () => {})
  refreshRef.current = async (trigger, scroller) => {
    if (inFlight) return
    inFlight = true
    const { product, screen, client } = live.current
    setPhase({ pull: HOLD_PX, refreshing: true, ...place(scroller) })
    if (scroller && !reduced()) slide(scroller, HOLD_PX, true)
    try {
      const done = await refetchScreen(client)
      track('screen_refresh_done', { product, screen, trigger, ...done })
    } finally {
      inFlight = false
      if (scroller) slide(scroller, 0, true)
      setPhase(null)
    }
  }

  // The pull gesture. touchmove is not passive: once a pull has started, the page must not scroll or bounce under it.
  useEffect(() => {
    if (!enabled) return
    let start: { x: number; y: number; at: number; scroller: HTMLElement } | null = null
    let pulling = false
    let distance = 0
    const reset = () => {
      start = null
      pulling = false
      distance = 0
    }
    const down = (event: TouchEvent) => {
      reset()
      if (inFlight || event.touches.length !== 1 || !canPull()) return
      const target = event.target as Element | null
      const scroller = findScroller(nav.current)
      if (!target || !scroller || !scroller.contains(target) || target.closest(NO_PULL) || scroller.scrollTop > 0) return
      // A scroller inside the screen's own that is not at its top scrolls first.
      for (let at: Element | null = target; at && at !== scroller; at = at.parentElement) if (at.scrollTop > 0) return
      scroller.style.overscrollBehaviorY = 'contain'
      start = { x: event.touches[0].clientX, y: event.touches[0].clientY, at: Date.now(), scroller }
    }
    const move = (event: TouchEvent) => {
      if (!start) return
      const dx = event.touches[0].clientX - start.x
      const dy = event.touches[0].clientY - start.y
      if (!pulling) {
        // Sideways first (a strip, a photo swipe) or upwards: not a pull, leave the gesture alone.
        if (Math.abs(dx) > Math.abs(dy) || dy <= 0 || start.scroller.scrollTop > 0) {
          if (Math.abs(dx) > 6 || dy < -6) start = null
          return
        }
        if (dy < 6) return
        pulling = true
      }
      if (event.cancelable) event.preventDefault()
      distance = rubber(dy)
      if (!reduced()) slide(start.scroller, distance, false)
      setPhase({ pull: distance, refreshing: false, ...place(start.scroller) })
    }
    const up = () => {
      if (!start) return
      const { scroller, at } = start
      const wasPulling = pulling
      const pulled = Math.round(distance)
      reset()
      if (!wasPulling) return
      const { product, screen } = live.current
      const refreshed = pulled >= PULL_THRESHOLD && !inFlight
      track('pull_to_refresh', { product, screen, pulled_px: pulled, result: refreshed ? 'refreshed' : 'cancelled', duration_ms: Date.now() - at, surface: surface() })
      if (refreshed) void refreshRef.current('pull', scroller)
      else {
        slide(scroller, 0, true)
        setPhase(null)
      }
    }
    document.addEventListener('touchstart', down, { passive: true })
    document.addEventListener('touchmove', move, { passive: false })
    document.addEventListener('touchend', up)
    document.addEventListener('touchcancel', up)
    return () => {
      document.removeEventListener('touchstart', down)
      document.removeEventListener('touchmove', move)
      document.removeEventListener('touchend', up)
      document.removeEventListener('touchcancel', up)
    }
  }, [enabled, nav])

  // The tab you are on, tapped again: back to the top if scrolled, then refresh.
  const reselect = (tab: string) => {
    const scroller = findScroller(nav.current)
    const from = Math.round(scroller?.scrollTop ?? 0)
    const { product } = live.current
    track('nav_tab_reselect', { product, tab, scrolled_from_px: from, action: from > 0 ? 'scroll_top' : 'refresh', surface: surface() })
    if (!scroller || from <= 0) return void refreshRef.current('tab', scroller)
    scroller.scrollTo({ top: 0, behavior: reduced() ? 'auto' : 'smooth' })
    const began = Date.now()
    const wait = () => {
      if (scroller.scrollTop <= 0 || Date.now() - began > 900) void refreshRef.current('tab', scroller)
      else requestAnimationFrame(wait)
    }
    requestAnimationFrame(wait)
  }

  api.current = reselect
  return phase && enabled ? createPortal(<PullIndicator phase={phase} />, document.body) : null
}

function place(scroller: HTMLElement | null) {
  if (!scroller) return { top: 0, left: 0, width: window.innerWidth }
  const box = scroller.getBoundingClientRect()
  return { top: box.top, left: box.left, width: box.width }
}

// Moves the screen's content down with the finger, and eases it back. The children move, not the scroller, so the
// scroller clips them and nothing slides over the nav or the footer.
function slide(scroller: HTMLElement, px: number, animate: boolean) {
  for (const child of scroller.children as HTMLCollectionOf<HTMLElement>) {
    child.style.transition = animate ? 'transform 220ms ease-out' : 'none'
    child.style.transform = px ? `translateY(${px}px)` : ''
  }
}

function PullIndicator({ phase }: { phase: Phase }) {
  const progress = Math.min(1, phase.pull / PULL_THRESHOLD)
  const still = reduced()
  const y = still ? 8 : Math.max(4, (phase.pull - 36) / 2)
  return (
    <div
      data-testid="pull-indicator"
      data-state={phase.refreshing ? 'refreshing' : progress >= 1 ? 'armed' : 'pulling'}
      role="status"
      aria-label="به‌روزرسانی"
      className="pointer-events-none fixed z-50 flex justify-center"
      style={{ top: phase.top + y, left: phase.left, width: phase.width }}
    >
      <span className="flex size-9 items-center justify-center rounded-full border border-border bg-card text-primary shadow-md" style={{ opacity: phase.refreshing ? 1 : progress }}>
        <RefreshCw className={`size-5${phase.refreshing ? ' animate-spin' : ''}`} style={phase.refreshing || still ? undefined : { transform: `rotate(${phase.pull * 4}deg)` }} aria-hidden />
      </span>
    </div>
  )
}

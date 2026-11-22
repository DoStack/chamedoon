import { lazy, Suspense, useEffect, useRef, useState, type ComponentType, type RefObject } from 'react'
import type { Product, PullProps } from './pull-refresh-impl'

const load = () => import('./pull-refresh-impl')

// Offline before the chunk lands: the screen carries on without the gesture, and it comes back once the connection does.
function NotLoaded(props: PullProps) {
  const [Impl, setImpl] = useState<ComponentType<PullProps> | null>(null)
  useEffect(() => {
    const retry = () => void load().then((module) => setImpl(() => module.default), () => {})
    window.addEventListener('online', retry)
    return () => window.removeEventListener('online', retry)
  }, [])
  return Impl ? <Impl {...props} /> : null
}

const PullRefresh = lazy(() => load().catch(() => ({ default: NotLoaded })))

// Tab reselect and pull-to-refresh for a tabbed screen. The gesture and its indicator load in their own chunk right
// after the nav; a tap before that lands is replayed once it has.
export function usePullRefresh(props: { nav: RefObject<HTMLElement | null>; product: Product; screen: string; enabled: boolean }) {
  const api = useRef<((tab: string) => void) | null>(null)
  const reselect = (tab: string) => {
    if (api.current) return api.current(tab)
    load().then(() => setTimeout(() => api.current?.(tab), 50), () => {})
  }
  const indicator = props.enabled ? (
    <Suspense fallback={null}>
      <PullRefresh {...props} api={api} />
    </Suspense>
  ) : null
  return { reselect, indicator }
}

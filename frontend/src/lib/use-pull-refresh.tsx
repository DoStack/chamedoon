import { lazy, Suspense, useRef, type RefObject } from 'react'
import type { Product } from './pull-refresh-impl'

const PullRefresh = lazy(() => import('./pull-refresh-impl'))

// Tab reselect and pull-to-refresh for a tabbed screen. The gesture and its indicator load in their own chunk right
// after the nav; a tap before that lands is replayed once it has.
export function usePullRefresh(props: { nav: RefObject<HTMLElement | null>; product: Product; screen: string; enabled: boolean }) {
  const api = useRef<((tab: string) => void) | null>(null)
  const reselect = (tab: string) => {
    if (api.current) return api.current(tab)
    void import('./pull-refresh-impl').then(() => setTimeout(() => api.current?.(tab), 50))
  }
  const indicator = props.enabled ? (
    <Suspense fallback={null}>
      <PullRefresh {...props} api={api} />
    </Suspense>
  ) : null
  return { reselect, indicator }
}

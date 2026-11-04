import { lazy, Suspense, useEffect, useState } from 'react'
import { Navigate, Outlet, createBrowserRouter, useNavigate } from 'react-router-dom'
import { RouteErrorBoundary } from '@/components/error-boundary'
import { getSavedCity, restoreSavedCity } from '@/lib/city'
import { useFeature, useMapOn } from '@/lib/features'
import { resolveStart } from '@/lib/start'
import { getStartParam, getWebApp } from '@/lib/telegram'
import { DetailScreen } from '@/screens/detail'
import { CityScreen } from '@/screens/first-run'
import { CheckingScreen } from '@/screens/global-states'
import { HomeScreen } from '@/screens/home'
import { Gated } from '@/screens/p3-gate'
import { POST_ROUTES } from '@/screens/post-gate'
import { SavedScreen } from '@/screens/saved'

const AlertsHub = lazy(() => import('@/screens/alerts'))
const AlertListingsScreen = lazy(() => import('@/screens/alert-listings'))
const AccountScreen = lazy(() => import('@/screens/account'))
const SafetyScreen = lazy(() => import('@/screens/safety'))
const InboxScreen = lazy(() => import('@/screens/inbox'))
const NotifSettingsScreen = lazy(() => import('@/screens/notif-settings'))

const TextSearchScreen = lazy(() => import('@/screens/text-search').then((module) => ({ default: module.TextSearchScreen })))

const MapScreen = lazy(() => import('@/screens/map').then((module) => ({ default: module.Component })))

// Off: the same as an unknown path, and the map library never loads.
function MapRoute() {
  const mapOn = useMapOn()
  if (!mapOn) return <Navigate to="/" replace />
  return (
    <Suspense fallback={<CheckingScreen />}>
      <MapScreen />
    </Suspense>
  )
}

// With alerts off the saved tab is F14 alone, as it was.
function SavedRoute() {
  const alerts = useFeature('alerts')
  if (!alerts) return <SavedScreen />
  return (
    <Suspense fallback={<CheckingScreen />}>
      <AlertsHub />
    </Suspense>
  )
}

function filterQuery(filters: { types: string[]; min: number | null; max: number | null }) {
  const params = new URLSearchParams()
  for (const type of filters.types) params.append('types', type)
  if (filters.min != null) params.set('min', String(filters.min))
  if (filters.max != null) params.set('max', String(filters.max))
  return params
}

// A full page load can come back here during one launch (from a template page), with the same start param in initData.
// A webview can keep sessionStorage across launches, and each launch signs a new auth_date, so reopening the link applies it.
function startAlreadyUsed() {
  const launch = new URLSearchParams(getWebApp()?.initData ?? '').get('auth_date') ?? ''
  const value = `${getStartParam()}|${launch}`
  try {
    if (sessionStorage.getItem('chamedoon.start') === value) return true
    sessionStorage.setItem('chamedoon.start', value)
  } catch {
    // No storage: the link applies again, which only repeats a navigation.
  }
  return false
}

// Applies the launch link once, before the first screen, so a link never flashes the city question. A template link can
// already have brought the person to another path of the shell: that path wins, unless it is the link's own listing.
function useStartRoute() {
  const navigate = useNavigate()
  const [ready, setReady] = useState(false)
  useEffect(() => {
    void (async () => {
      await restoreSavedCity()
      const found = await resolveStart()
      const link = found && !startAlreadyUsed() ? found : null
      const here = location.pathname.replace(/^\/app\/v2\/?/, '')
      if (link?.kind === 'legacy') {
        location.assign(link.legacy_path + location.hash)
        return
      }
      const fits = here === '' || (link?.kind === 'listing' && here === `listing/${link.listing_id}`)
      switch (fits ? link?.kind : undefined) {
        case 'country':
          if (!getSavedCity()) navigate(`/choose?country=${link!.country}`, { replace: true })
          break
        case 'city': {
          if (link!.city_known) {
            const params = filterQuery(link!.filters)
            params.set('city', link!.city_slug)
            navigate(`/?${params}`, { replace: true })
          }
          break
        }
        case 'listing':
          if (link!.listing_id) navigate(`/listing/${link!.listing_id}`, { replace: true, state: { fromLink: true, link } })
          break
      }
      setReady(true)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return ready
}

function Shell() {
  const ready = useStartRoute()
  return (
    <div className="app-shell">
      <div className="app-top" />
      <main className="app-main" data-testid={ready ? 'app-ready' : 'checking'}>
        {ready ? <Outlet /> : <CheckingScreen />}
      </main>
      <div className="app-bottom" />
    </div>
  )
}

export const router = createBrowserRouter(
  [
    {
      element: <Shell />,
      children: [
        {
          // A crash in one flow keeps the shell around it.
          errorElement: <RouteErrorBoundary />,
          children: [
            { index: true, element: <HomeScreen /> },
            { path: 'choose', element: <CityScreen /> },
            { path: 'saved', element: <SavedRoute /> },
            { path: 'alerts/:id', element: <Gated feature="alerts"><AlertListingsScreen /></Gated> },
            { path: 'account', element: <Gated feature="account"><AccountScreen /></Gated> },
            { path: 'safety', element: <Gated feature="account"><SafetyScreen /></Gated> },
            { path: 'inbox', element: <Gated feature="account"><InboxScreen /></Gated> },
            { path: 'settings/notifications', element: <Gated feature="account"><NotifSettingsScreen /></Gated> },
            { path: 'search', element: <Suspense fallback={<CheckingScreen />}><TextSearchScreen /></Suspense> },
            { path: 'listing/:id', element: <DetailScreen /> },
            { path: 'map', element: <MapRoute /> },
            ...POST_ROUTES,
            ...(__TG_MOCK__ ? [{ path: '__harness/:action', lazy: () => import('./test-harness') }] : []),
            { path: '*', element: <Navigate to="/" replace /> },
          ],
        },
      ],
    },
  ],
  { basename: '/app/v2' },
)

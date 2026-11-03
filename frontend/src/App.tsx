import { useEffect, useState } from 'react'
import { Navigate, Outlet, createBrowserRouter, useNavigate } from 'react-router-dom'
import { RouteErrorBoundary } from '@/components/error-boundary'
import { getSavedCity, restoreSavedCity } from '@/lib/city'
import { resolveStart } from '@/lib/start'
import { CityScreen } from '@/screens/first-run'
import { HomeScreen } from '@/screens/home'

function filterQuery(filters: { types: string[]; min: number | null; max: number | null }) {
  const params = new URLSearchParams()
  for (const type of filters.types) params.append('types', type)
  if (filters.min != null) params.set('min', String(filters.min))
  if (filters.max != null) params.set('max', String(filters.max))
  return params
}

// Applies the launch link once, before the first screen, so a link never flashes the city question.
function useStartRoute() {
  const navigate = useNavigate()
  const [ready, setReady] = useState(false)
  useEffect(() => {
    void (async () => {
      await restoreSavedCity()
      const link = await resolveStart()
      switch (link?.kind) {
        case 'legacy':
          location.assign(link.legacy_path + location.hash)
          return
        case 'country':
          if (!getSavedCity()) navigate(`/choose?country=${link.country}`, { replace: true })
          break
        case 'city': {
          if (link.city_known) {
            const params = filterQuery(link.filters)
            params.set('city', link.city_slug)
            navigate(`/?${params}`, { replace: true })
          }
          break
        }
        case 'listing':
          if (link.listing_id) navigate(`/listing/${link.listing_id}`, { replace: true, state: { fromLink: true, link } })
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
        {ready ? <Outlet /> : null}
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
            ...(__TG_MOCK__ ? [{ path: '__harness/:action', lazy: () => import('./test-harness') }] : []),
            { path: '*', element: <Navigate to="/" replace /> },
          ],
        },
      ],
    },
  ],
  { basename: '/app/v2' },
)

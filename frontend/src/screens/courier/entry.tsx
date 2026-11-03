import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode, useEffect, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { Navigate, Outlet, RouterProvider, createBrowserRouter } from 'react-router-dom'
import { AppErrorBoundary, RouteErrorBoundary } from '@/components/error-boundary'
import { DirectionProvider } from '@/components/ui/direction'
import { Toaster } from '@/components/ui/sonner'
import { boot, useAccess } from './access'
import { errorCode, isRetryable } from './api'
import { GuideScreen } from './guide'
import { BlockedScreen, BootFailed, BootOffline, Checking, MaintenanceScreen, NotFoundScreen, OutsideScreen, SessionOverScreen, UpdateSheet } from './states'

const queryClient = new QueryClient({
  defaultOptions: {
    // `always`: offline, a call fails at once and reaches its error state, instead of pausing on a skeleton.
    queries: { staleTime: 30_000, networkMode: 'always', retry: (count, error) => count < 1 && isRetryable(error) && errorCode(error) !== 'offline' },
  },
})

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <div className="app-top" />
      <main className="app-main" data-testid="courier-app">
        {children}
      </main>
      <div className="app-bottom" />
    </div>
  )
}

const router = createBrowserRouter(
  [
    {
      element: (
        <Frame>
          <Outlet />
        </Frame>
      ),
      children: [
        {
          errorElement: <RouteErrorBoundary />,
          children: [
            { index: true, element: <Navigate to="/guide" replace /> },
            { path: 'guide', element: <GuideScreen /> },
            { path: '*', element: <NotFoundScreen /> },
          ],
        },
      ],
    },
  ],
  { basename: '/app/v2/courier' },
)

function Boot({ children }: { children: ReactNode }) {
  const { access, update } = useAccess()
  useEffect(() => {
    void boot()
  }, [])
  const blocked = (() => {
    switch (access) {
      case 'open':
        return null
      case 'outside':
        return <OutsideScreen />
      case 'session_over':
        return <SessionOverScreen />
      case 'blocked':
        return <BlockedScreen />
      case 'maintenance':
        return <MaintenanceScreen />
      case 'offline':
        return <BootOffline />
      case 'failed':
        return <BootFailed />
      default:
        return <Checking />
    }
  })()
  return (
    <>
      {blocked ? <Frame>{blocked}</Frame> : children}
      <UpdateSheet update={update} />
      <Toaster position="bottom-left" dir="rtl" offset={{ bottom: 'calc(100dvh - var(--app-h) + var(--toast-b) + var(--safe-b))', left: 24, top: 'calc(var(--safe-t) + 12px)' }} mobileOffset={{ bottom: 'calc(100dvh - var(--app-h) + var(--toast-b) + var(--safe-b))', left: 16, right: 16, top: 'calc(var(--safe-t) + 12px)' }} />
    </>
  )
}

export function mountCourier() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <DirectionProvider dir="rtl">
        <AppErrorBoundary>
          <QueryClientProvider client={queryClient}>
            <Boot>
              <RouterProvider router={router} />
            </Boot>
          </QueryClientProvider>
        </AppErrorBoundary>
      </DirectionProvider>
    </StrictMode>,
  )
}

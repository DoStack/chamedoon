import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode, useEffect, useState, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { Outlet, RouterProvider, createBrowserRouter, useNavigate } from 'react-router-dom'
import { AppErrorBoundary, RouteErrorBoundary } from '@/components/error-boundary'
import { DirectionProvider } from '@/components/ui/direction'
import { Toaster } from '@/components/ui/sonner'
import { boot, useAccess } from './access'
import { AccountScreen } from './account'
import { errorCode, isRetryable } from './api'
import { AlertsScreen } from './alerts'
import { IntroScreen } from './contact'
import { GuideScreen } from './guide'
import { InboxScreen } from './inbox'
import { MatchScreen } from './match'
import { MineDetailScreen, MineEditScreen, MineScreen } from './mine'
import { PostChooser, PostPackage, PostTrip } from './post'
import { PostedScreen } from './posted'
import { RequestScreen } from './request'
import { RoutesScreen } from './routes'
import { resolveStart } from './start'
import { NewTicketScreen, TicketScreen, TicketsScreen } from './support'
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

// Applies the launch link once, before the first screen, so a link never flashes the question.
function Shell() {
  const navigate = useNavigate()
  const [ready, setReady] = useState(false)
  useEffect(() => {
    void (async () => {
      const landing = await resolveStart()
      if (landing?.external) {
        location.assign(`${landing.path}${location.search}${location.hash}`)
        return
      }
      if (landing) navigate(landing.path, { replace: true, state: landing.state })
      setReady(true)
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return <Frame>{ready ? <Outlet /> : <Checking />}</Frame>
}

const router = createBrowserRouter(
  [
    {
      element: <Shell />,
      children: [
        {
          errorElement: <RouteErrorBoundary />,
          children: [
            { index: true, element: <RoutesScreen /> },
            { path: 'guide', element: <GuideScreen /> },
            { path: 'request/:id', element: <RequestScreen /> },
            { path: 'post', element: <PostChooser /> },
            { path: 'post/package', element: <PostPackage /> },
            { path: 'post/trip', element: <PostTrip /> },
            { path: 'posted/:id', element: <PostedScreen /> },
            { path: 'alerts', element: <AlertsScreen /> },
            { path: 'me', element: <AccountScreen /> },
            { path: 'me/support', element: <TicketsScreen /> },
            { path: 'me/support/new', element: <NewTicketScreen /> },
            { path: 'me/support/:id', element: <TicketScreen /> },
            { path: 'me/requests', element: <MineScreen /> },
            { path: 'me/requests/:id', element: <MineDetailScreen /> },
            { path: 'me/requests/:id/edit', element: <MineEditScreen /> },
            { path: 'me/matches', element: <InboxScreen /> },
            { path: 'me/intros/:id', element: <IntroScreen /> },
            { path: 'me/matches/:id', element: <MatchScreen /> },
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

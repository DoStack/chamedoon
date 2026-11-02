import { Outlet, createBrowserRouter } from 'react-router-dom'
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'

function Shell() {
  return (
    <div className="app-shell">
      <div className="app-top" />
      <main className="app-main">
        <Outlet />
      </main>
      <div className="app-bottom" />
    </div>
  )
}

function Home() {
  return (
    <Empty className="h-full">
      <EmptyHeader>
        <EmptyMedia>
          <span className="brand-logo brand-logo-fa h-14" />
        </EmptyMedia>
        <EmptyTitle role="heading" aria-level={1}>
          چمدون
        </EmptyTitle>
      </EmptyHeader>
    </Empty>
  )
}

export const router = createBrowserRouter(
  [
    {
      element: <Shell />,
      children: [
        { index: true, element: <Home /> },
        { path: '*', element: <Home /> },
      ],
    },
  ],
  { basename: '/app/v2' },
)

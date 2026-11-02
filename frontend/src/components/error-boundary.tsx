import { RefreshCw, ServerCrash } from 'lucide-react'
import { Component, useEffect, type ReactNode } from 'react'
import { useRouteError } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { report } from '@/lib/report'

// Copy and layout from F25, server error state. The error itself never reaches this screen.
function CrashScreen() {
  return (
    <Empty className="h-full" data-testid="crash-screen">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <ServerCrash />
        </EmptyMedia>
        <EmptyTitle>مشکلی از طرف ما پیش آمد</EmptyTitle>
      </EmptyHeader>
      <EmptyContent>
        <Button className="w-full" onClick={() => window.location.reload()}>
          <RefreshCw />
          تلاش مجدد
        </Button>
      </EmptyContent>
    </Empty>
  )
}

function describeError(error: unknown) {
  return error instanceof Error ? { name: error.name, message: error.message, stack: error.stack } : { value: String(error) }
}

// Outermost net: catches what escapes the router, including a broken route fallback.
export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: unknown) {
    report('app_crash', describeError(error))
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="app-shell">
        <div className="app-top" />
        <main className="app-main">
          <CrashScreen />
        </main>
        <div className="app-bottom" />
      </div>
    )
  }
}

// `errorElement` for routes. Without one, React Router shows its default page with the raw error.
export function RouteErrorBoundary() {
  const error = useRouteError()
  useEffect(() => {
    report('route_crash', describeError(error))
  }, [error])
  return <CrashScreen />
}

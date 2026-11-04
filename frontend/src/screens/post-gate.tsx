import { Suspense, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { usePosting } from '@/lib/posting'
import { CheckingScreen } from './global-states'

// A P4 screen behind its flag. Off, the path acts like any unknown path, and the screen's code is never fetched: the
// lazy() element below this only renders when it is on.
export function Gated({ children }: { children: ReactNode }) {
  const on = usePosting()
  if (!on) return <Navigate to="/" replace />
  return <Suspense fallback={<CheckingScreen />}>{children}</Suspense>
}

export const POST_ROUTES: Array<{ path: string; element: ReactNode }> = []

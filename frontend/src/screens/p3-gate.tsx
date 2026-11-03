import { Suspense, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useFeature, type Feature } from '@/lib/features'
import { CheckingScreen } from './global-states'

// A P3 screen behind its flag. Off, the path acts like any unknown path, and the screen's code is never fetched: the
// lazy() element below this only renders when it is on.
export function Gated({ feature, children }: { feature: Feature; children: ReactNode }) {
  const on = useFeature(feature)
  if (!on) return <Navigate to="/" replace />
  return <Suspense fallback={<CheckingScreen />}>{children}</Suspense>
}

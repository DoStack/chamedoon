import { useSyncExternalStore } from 'react'
import type { Meta } from '@/api/types'
import { getSession, subscribeSession } from './api'
import { useAccess } from './access'
import { MAP_ON } from './flags'

export type Feature = 'alerts' | 'account'

// Missing means off: an old API, an unread meta or an offline open all show the app as it was before P3.
export function featureOn(meta: Pick<Meta, 'features'> | null | undefined, name: Feature) {
  return meta?.features?.[name] === true
}

// A `feature_off` from the server is newer than a cached meta, so the entry points leave for this session (F25#5).
const closed = new Set<Feature>()
const listeners = new Set<() => void>()
let version = 0

export function closeFeature(name: Feature) {
  if (closed.has(name)) return
  closed.add(name)
  version += 1
  listeners.forEach((listener) => listener())
}

function useClosedVersion() {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => void listeners.delete(listener)
    },
    () => version,
  )
}

export function useFeature(name: Feature) {
  const { meta } = useAccess()
  useClosedVersion()
  return featureOn(meta, name) && !closed.has(name)
}

// F15#7: no way to make an alert unless messages are going out. The saved ones can still be managed.
export function useAlertEntry() {
  const { meta } = useAccess()
  const alerts = useFeature('alerts')
  return alerts && meta?.alerts_on === true
}

// Browsing alone never exchanges (ADR-3), so what needs a person waits for a session that already exists.
export function useHasSession() {
  return useSyncExternalStore(subscribeSession, () => getSession() !== null)
}

// The map's own switch ships in the bundle. `features.map` comes only from a QA person's exchange.
export function useMapOn() {
  const { meta } = useAccess()
  return (MAP_ON || (meta?.features as { map?: boolean } | undefined)?.map === true) && Boolean(meta?.map_style_url)
}

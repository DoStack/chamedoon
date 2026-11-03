import { useSyncExternalStore } from 'react'
import type { Meta } from '@/api/types'
import { useAccess } from './access'

export type PostingFeature = 'posting' | 'smart_posting'

// Missing means off: an old API, an unread meta or an offline open all show the app as it was before P4.
export function postingOn(meta: Pick<Meta, 'features'> | null | undefined) {
  return meta?.features?.posting === true
}

// Reading text needs both flags on the server, so it needs both here.
export function smartPostingOn(meta: Pick<Meta, 'features'> | null | undefined) {
  return postingOn(meta) && meta?.features?.smart_posting === true
}

// A `feature_off` from the server is newer than a cached meta, so the entry points leave for this session.
const closed = new Set<PostingFeature>()
const listeners = new Set<() => void>()
let version = 0

export function closePosting(name: PostingFeature) {
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

export function usePosting() {
  const { meta } = useAccess()
  useClosedVersion()
  return postingOn(meta) && !closed.has('posting')
}

export function useSmartPosting() {
  const { meta } = useAccess()
  useClosedVersion()
  return smartPostingOn(meta) && !closed.has('posting') && !closed.has('smart_posting')
}

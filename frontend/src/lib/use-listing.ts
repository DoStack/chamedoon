import { useQuery } from '@tanstack/react-query'
import { useEffect, useSyncExternalStore } from 'react'
import type { Listing, ViewerListing } from '@/api/types'
import { ApiFailureError, getSession, meFetch, publicFetch, subscribeSession, unwrap } from './api'
import { overrideCard } from './overrides'

// The cached public detail for everyone, and the uncached viewer copy once a session exists: it says whose listing this
// is and lets an owner open it while hidden. Browsing alone never exchanges (ADR-3).
export function useListing(id: number) {
  const signedIn = useSyncExternalStore(subscribeSession, () => getSession() !== null)
  const pub = useQuery({ queryKey: ['listing', id], queryFn: () => unwrap(publicFetch<Listing>(`/api/v1/public/listings/${id}/`)), enabled: Number.isInteger(id) })
  const viewer = useQuery({ queryKey: ['viewer', id], queryFn: () => unwrap(meFetch<ViewerListing>(`/api/v1/me/listings/${id}/`)), enabled: signedIn && Number.isInteger(id), staleTime: 0 })

  const code = (error: unknown) => (error instanceof ApiFailureError ? error.failure.code : null)
  const publicGone = code(pub.error) === 'listing_gone'
  const listing = viewer.data?.listing ?? pub.data ?? null

  useEffect(() => {
    if (listing) overrideCard({ ...listing, closed: listing.state === 'closed' })
  }, [listing])

  return {
    listing,
    isOwner: viewer.data?.is_owner ?? false,
    reviewState: viewer.data?.review_state ?? 'live',
    loading: !listing && (pub.isPending || (publicGone && signedIn && viewer.isPending)),
    // F02#4. Only after the viewer copy had its say, since an owner can still open a hidden listing.
    gone: !listing && publicGone && (!signedIn || viewer.isError),
    failed: !listing && pub.isError && !publicGone,
    retry: () => void pub.refetch(),
  }
}

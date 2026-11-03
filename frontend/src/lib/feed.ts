import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { SNAPSHOT_SCHEMA, type Card, type Feed, type Manifest, type Meta, type SnapshotFile } from '@/api/types'
import { ApiFailureError, publicFetch, unwrap } from './api'
import { getMeta, setUpdateNeeded } from './access'
import { readSnapshot, writeSnapshot } from './snapshot-cache'

export type FeedPage = { cards: Card[]; generatedAt: string; nextCursor: string | null; source: 'snapshot' | 'feed' }

function fromFile(file: SnapshotFile): FeedPage {
  return { cards: file.listings, generatedAt: file.generated_at, nextCursor: null, source: 'snapshot' }
}

// Null on anything short of a good file, so a snapshot that fails or is malformed only means the fallback feed.
async function loadSnapshot(slug: string, cached: SnapshotFile | null): Promise<FeedPage | null> {
  // `manifest_url` is in the contract of #56. Blank or missing: no snapshots here.
  const meta = getMeta() as (Meta & { manifest_url?: string }) | null
  if (!meta?.manifest_url || meta.snapshots_on === false) return null
  const manifest = await publicFetch<Manifest>(meta.manifest_url, { timeoutMs: 5_000 })
  if (!manifest.ok || !Array.isArray(manifest.data?.cities)) return null
  // F25#5: this bundle cannot read a newer format, and the fallback feed keeps it useful until the reload.
  if (manifest.data.schema_version > SNAPSHOT_SCHEMA) {
    setUpdateNeeded()
    return null
  }
  const row = manifest.data.cities.find((city) => city.city_slug === slug)
  if (!row) return null
  if (cached?.version === row.version) return fromFile(cached)
  const file = await publicFetch<SnapshotFile>(row.url, { timeoutMs: 8_000 })
  if (!file.ok || file.data?.schema_version > SNAPSHOT_SCHEMA || !Array.isArray(file.data?.listings)) return null
  void writeSnapshot(file.data)
  return fromFile(file.data)
}

async function loadFallback(slug: string, country: string, cursor: string | null, timeoutMs = 15_000): Promise<FeedPage> {
  const params = new URLSearchParams({ city: slug, country })
  if (cursor) params.set('cursor', cursor)
  const data = await unwrap(publicFetch<Feed>(`/api/v1/public/feed/?${params}`, { timeoutMs }))
  return { cards: data.results, generatedAt: data.generated_at, nextCursor: data.next_cursor, source: 'feed' }
}

// Try order per F04: the current snapshot, then the fallback feed. Offline with a cached file, that file keeps showing.
export function useFeed(slug: string, country: string) {
  const cached = useQuery({ queryKey: ['snapshot-cache', slug], queryFn: () => readSnapshot(slug), staleTime: Infinity, gcTime: 0, enabled: Boolean(slug) })
  const feed = useInfiniteQuery({
    queryKey: ['feed', slug],
    enabled: Boolean(slug),
    initialPageParam: null as string | null,
    retry: false,
    queryFn: async ({ pageParam }) => {
      if (pageParam) return loadFallback(slug, country, pageParam)
      // The whole try fits the 15 s of F25, with a little kept for the fallback.
      const deadline = Date.now() + 15_000
      const snapshot = await loadSnapshot(slug, await readSnapshot(slug))
      return snapshot ?? loadFallback(slug, country, null, Math.max(2_000, deadline - Date.now()))
    },
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  })
  const fromCache = !feed.data && cached.data ? fromFile(cached.data) : null
  const pages = feed.data?.pages ?? (fromCache ? [fromCache] : [])
  const cards = pages.flatMap((page) => page.cards)
  const failure = feed.error instanceof ApiFailureError ? feed.error.failure : null
  return {
    cards,
    hasData: pages.length > 0,
    generatedAt: pages[0]?.generatedAt ?? null,
    loading: pages.length === 0 && !feed.isError,
    failed: pages.length === 0 && feed.isError,
    failure,
    refreshing: feed.isRefetching,
    refresh: () => feed.refetch(),
    hasMore: feed.hasNextPage,
    loadingMore: feed.isFetchingNextPage,
    moreFailed: feed.isFetchNextPageError,
    loadMore: () => feed.fetchNextPage(),
  }
}

const STALE_HOURS = 24

// F04#10: hours since the list was written, when that is past the threshold.
export function staleHours(generatedAt: string | null, now = Date.now()) {
  if (!generatedAt) return null
  const hours = Math.floor((now - new Date(generatedAt).getTime()) / 3_600_000)
  return hours > STALE_HOURS ? hours : null
}

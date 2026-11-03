import type { AlertRow } from '@/api/types'
import { useFeed } from '@/lib/feed'
import type { View } from '@/lib/listing-view'
import { FilterSheet } from './filter-sheet'

// F16: «فیلتر» in the edit sheet is F06 on the alert's own city, whichever city is open now.
export function AlertFilterSheet({ row, view, onClose, onApply }: { row: AlertRow; view: View; onClose: () => void; onApply: (next: View) => void }) {
  const feed = useFeed(row.city_slug, row.country)
  return <FilterSheet open focus={null} onClose={onClose} cards={feed.cards} view={view} country={row.country} region={feed.cards[0]?.region ?? ''} partial={feed.hasMore || !feed.hasData} onApply={(next) => {
    onApply(next)
    onClose()
  }} />
}

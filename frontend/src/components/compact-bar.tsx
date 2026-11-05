import { ChevronDown, SlidersHorizontal } from 'lucide-react'
import type { InputHTMLAttributes, ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InputGroup, InputGroupInput } from '@/components/ui/input-group'
import { fa } from '@/lib/format'
import { t } from '@/lib/locale'

export type CompactIds = { city: string; search: string; filter: string }

// One row: search with the city inside it, the filter icon with its count, and whatever the screen adds after it.
// The list's sticky row and the map's top row.
export function CompactBar({ city, onCity, input, filters, onFilter, ids, children }: { city: string; onCity: () => void; input: InputHTMLAttributes<HTMLInputElement>; filters: number; onFilter: () => void; ids: CompactIds; children?: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">
        <InputGroup>
          <button type="button" onClick={onCity} data-testid={ids.city} className="flex h-full max-w-[55%] flex-none items-center gap-1 rounded-s-lg border-e border-input ps-3 pe-2 text-sm font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
            <span className="truncate">{city}</span>
            <ChevronDown className="size-4 flex-none text-muted-foreground" aria-hidden />
          </button>
          <InputGroupInput aria-label={t('جستجو')} placeholder={t('جستجو')} data-testid={ids.search} {...input} />
        </InputGroup>
      </div>
      <Button variant="outline" size="icon" aria-label={t('فیلتر')} className="relative flex-none" onClick={onFilter} data-testid={ids.filter}>
        <SlidersHorizontal />
        {filters ? <Badge className="pointer-events-none absolute -top-1 -end-1 h-4 min-w-4 px-1 text-[10px]">{fa(filters)}</Badge> : null}
      </Button>
      {children}
    </div>
  )
}

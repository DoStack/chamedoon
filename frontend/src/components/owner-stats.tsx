import { BadgeCheck } from 'lucide-react'
import type { OwnerStats } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { fa } from '@/lib/format'
import { t } from '@/lib/locale'

// Counts only, never who. «contacts» are people who took the contact details, not calls.
const ROWS = [
  { key: 'views', label: 'بازدید' },
  { key: 'saves', label: 'نشان‌کردن' },
  { key: 'contacts', label: 'اطلاعات تماس رو گرفتن' },
] as const

// The owner's view of a listing: this week and in total.
export function OwnerStatsCard({ stats }: { stats: OwnerStats }) {
  return (
    <Card className="gap-2 p-3" data-testid="owner-stats">
      <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-4 gap-y-1.5 text-sm">
        <span className="text-xs text-muted-foreground">آمار آگهی</span>
        <span className="text-xs text-muted-foreground">این هفته</span>
        <span className="text-xs text-muted-foreground">کل</span>
        {ROWS.map(({ key, label }) => (
          <div key={key} className="contents" data-testid={`stat-${key}`}>
            <span>{label}</span>
            <b className="text-center font-bold" data-testid={`stat-${key}-week`}>
              {fa(stats[`${key}_7d`])}
            </b>
            <b className="text-center font-bold" data-testid={`stat-${key}-total`}>
              {fa(stats[key])}
            </b>
          </div>
        ))}
      </div>
    </Card>
  )
}

// The same three counts in one line, for a row in «آگهی‌های من».
export function OwnerStatsLine({ stats }: { stats: OwnerStats }) {
  return (
    <div className="truncate text-[13px] text-muted-foreground" data-testid="owner-stats-line">
      {ROWS.map(({ key, label }) => `${label} ${fa(stats[key])}`).join(' · ')}
    </div>
  )
}

// «تأیید صاحب آگهی»: its owner signed in and opened it. Older snapshots lack the field, which reads as false.
export function OwnerVerifiedBadge() {
  return (
    <Badge variant="outline" data-testid="owner-verified">
      <BadgeCheck />
      {t('تأیید صاحب آگهی')}
    </Badge>
  )
}

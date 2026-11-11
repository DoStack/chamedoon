import { useQuery } from '@tanstack/react-query'
import { Handshake, PackageOpen, Scale, Search, Send, Star, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { track } from '@/lib/report'
import { courierFetch, unwrap } from './api'
import { H1, Page, Stack } from './page'
import { Chip } from './parts'
import type { Guide } from './types'

// Design v4: five steps, a title each, no sub lines.
const TIPS: Array<[LucideIcon, string]> = [
  [Search, 'مسیرتان را پیدا کنید'],
  [Handshake, 'درخواست معرفی بفرستید'],
  [Send, 'در تلگرام هماهنگ کنید'],
  [PackageOpen, 'مسافر بسته را باز می‌کند و می‌بیند'],
  [Star, 'بعد از تحویل امتیاز بدهید'],
]

export const GUIDE_KEY = ['courier', 'guide']

export function useGuide() {
  return useQuery({ queryKey: GUIDE_KEY, queryFn: () => unwrap(courierFetch<Guide>('guide/')), staleTime: 3_600_000 })
}

// C17.2. Opens from the guide, the request page and the post forms; the list is admin-editable, so it comes from the server.
export function ProhibitedSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const guide = useGuide()
  useEffect(() => {
    if (open) track('courier.c17.prohibited')
    if (open) track('courier_prohibited_open')
  }, [open])
  return (
    <AppSheet open={open} onClose={onClose} label="چه چیزهایی ممنوع است؟" title="چه چیزهایی ممنوع است؟" testId="prohibited-sheet">
      {guide.data ? (
        <>
          <div className="flex flex-wrap gap-1.5">
            {guide.data.prohibited.map((line) => (
              <Chip key={line} tone="no">
                {line}
              </Chip>
            ))}
          </div>
          <p className="flex items-center gap-2 rounded-lg bg-muted px-3 py-2.5 text-[13px] font-semibold text-muted-foreground">
            <Scale className="size-4 shrink-0" aria-hidden />
            {guide.data.prohibited_footer}
          </p>
        </>
      ) : guide.isError ? (
        <div className="flex flex-col gap-2" data-testid="prohibited-failed">
          <p className="text-sm">فهرست بارگذاری نشد</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              track('courier_retry_tap', { where: 'prohibited' })
              void guide.refetch()
            }}
          >
            تلاش مجدد
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-4 w-[80%]" />
          <Skeleton className="h-4 w-[60%]" />
          <Skeleton className="h-4 w-[70%]" />
        </div>
      )}
    </AppSheet>
  )
}

export function ProhibitedButton({ variant = 'ghost', size = 'sm', full }: { variant?: 'ghost' | 'outline'; size?: 'sm' | 'default'; full?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant={variant} size={size} className={full ? 'w-full' : 'self-start'} onClick={() => setOpen(true)} data-testid="prohibited-open">
        <TriangleAlert />
        چه چیزهایی ممنوع است؟
      </Button>
      <ProhibitedSheet open={open} onClose={() => setOpen(false)} />
    </>
  )
}

// C17.1. The copy is the bot's guide and needs nothing from the server; only the prohibited list does.
export function GuideScreen() {
  useEffect(() => track('courier.c17.guide'), [])
  useEffect(() => track('courier_guide_view'), [])
  return (
    <Page tab="me" back="/me" testId="guide">
      <Stack center gap="gap-3">
        <H1>راهنما</H1>
        <Card className="!gap-0 !rounded-2xl !px-3 !py-0">
          {TIPS.map(([Icon, title]) => (
            <div key={title} className="flex items-center gap-2.5 border-b px-1 py-3 text-sm font-semibold last:border-b-0">
              <span className="flex size-[34px] flex-none items-center justify-center rounded-[10px] bg-muted">
                <Icon className="size-[18px]" aria-hidden />
              </span>
              {title}
            </div>
          ))}
        </Card>
        <ProhibitedButton variant="outline" size="default" full />
      </Stack>
    </Page>
  )
}

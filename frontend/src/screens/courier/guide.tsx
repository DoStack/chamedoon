import { useQuery } from '@tanstack/react-query'
import { Banknote, Bell, Luggage, MessageCircle, Package, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Item, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Skeleton } from '@/components/ui/skeleton'
import { track } from '@/lib/report'
import { courierFetch, unwrap } from './api'
import { H1, Page, Stack } from './page'
import type { Guide } from './types'

const TIPS: Array<[LucideIcon, string, string]> = [
  [Package, 'بسته دارم', 'مسیر، زمان و اندازه را بگویید؛ مسافرهای همان مسیر را نزدیک‌ترین تاریخ اول می‌بینید.'],
  [Luggage, 'مسافرم', 'تاریخ پرواز و جای خالی را بگویید؛ بسته‌های همان مسیر را می‌بینید.'],
  [MessageCircle, 'معرفی', 'به طرف مقابل پیام می‌دهیم؛ فقط اگر قبول کند آیدی‌ها رد و بدل می‌شود.'],
  [Bell, 'گوش به زنگ', 'تا ۵ مسیر؛ مسافر یا بسته‌ی تازه که بیاید خبرتان می‌کنیم.'],
  [Banknote, 'هزینه', 'هزینه را خودتان توافق می‌کنید؛ چمدون کمیسیون نمی‌گیرد.'],
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
  }, [open])
  return (
    <AppSheet open={open} onClose={onClose} label="چه چیزهایی ممنوع است؟" title="چه چیزهایی ممنوع است؟" testId="prohibited-sheet">
      {guide.data ? (
        <>
          <ul className="list-disc ps-5 text-sm leading-[22px]">
            {guide.data.prohibited.map((line) => (
              <li key={line} className="mb-1.5">
                {line}
              </li>
            ))}
          </ul>
          <p className="text-[13px] text-muted-foreground">{guide.data.prohibited_footer}</p>
        </>
      ) : guide.isError ? (
        <div className="flex flex-col gap-2" data-testid="prohibited-failed">
          <p className="text-sm">فهرست بارگذاری نشد</p>
          <Button variant="outline" size="sm" onClick={() => void guide.refetch()}>
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
  return (
    <Page tab="me" back="/me" testId="guide">
      <Stack center gap="gap-3">
        <H1>راهنما</H1>
        <Card className="!gap-0 !px-4 !py-1">
          <ItemGroup className="!gap-0">
            {TIPS.map(([Icon, title, text]) => (
              <Item key={title}>
                <ItemMedia variant="icon">
                  <Icon />
                </ItemMedia>
                <ItemContent>
                  <ItemTitle>{title}</ItemTitle>
                  <ItemDescription className="line-clamp-none">{text}</ItemDescription>
                </ItemContent>
              </Item>
            ))}
          </ItemGroup>
        </Card>
        <ProhibitedButton variant="outline" size="default" full />
      </Stack>
    </Page>
  )
}

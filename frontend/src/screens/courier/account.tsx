import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bell, Bot, ChevronLeft, ExternalLink, Info, LifeBuoy, List, Megaphone, TriangleAlert, Users, type LucideIcon } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Item, ItemActions, ItemContent, ItemGroup, ItemMedia, ItemTitle } from '@/components/ui/item'
import { Skeleton } from '@/components/ui/skeleton'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { patchJson, unwrap } from './api'
import { ProhibitedSheet, useGuide } from './guide'
import { applyTheme, H1, ME_KEY, Page, Stack, useMe } from './page'
import { Segmented } from './parts'
import { failedScreen, openLink } from './states'
import type { Me } from './types'

type Prefs = Me['prefs']
type Theme = 'auto' | 'light' | 'dark'

function Row({ icon: Icon, title, value, side, onClick, testId, chevron = true }: { icon: LucideIcon; title: string; value?: string; side?: ReactNode; onClick: () => void; testId: string; chevron?: boolean }) {
  return (
    <Item asChild>
      <button type="button" className="w-full text-start" onClick={onClick} data-testid={testId}>
        <ItemMedia variant="icon">
          <Icon />
        </ItemMedia>
        <ItemContent>
          <ItemTitle>{title}</ItemTitle>
        </ItemContent>
        <ItemActions>
          {side}
          {value ? <span className="text-sm text-muted-foreground">{value}</span> : null}
          {chevron ? <ChevronLeft className="size-4 text-muted-foreground" aria-hidden /> : null}
        </ItemActions>
      </button>
    </Item>
  )
}

function Group({ children }: { children: ReactNode }) {
  return (
    <Card className="!gap-0 !px-4 !py-1">
      <ItemGroup className="!gap-0">{children}</ItemGroup>
    </Card>
  )
}

// C15.2
function ChannelsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const guide = useGuide()
  const channels = guide.data?.channels ?? []
  return (
    <AppSheet open={open} onClose={onClose} label="کانال‌ها" title="کانال‌ها" testId="channels-sheet">
      {guide.isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : guide.isError ? (
        <div className="flex flex-col gap-2" data-testid="channels-failed">
          <p className="text-sm">فهرست بارگذاری نشد</p>
          <Button variant="outline" size="sm" onClick={() => void guide.refetch()}>
            تلاش مجدد
          </Button>
        </div>
      ) : (
        <Group>
          {guide.data?.main_channel ? <Row icon={Megaphone} title="کانال اصلی چمدون" side={<ExternalLink className="size-4" aria-hidden />} chevron={false} onClick={() => openLink(`https://t.me/${guide.data!.main_channel}`)} testId="channel-main" /> : null}
          {channels.map((channel) => (
            <Row key={channel.username} icon={Megaphone} title={channel.title || channel.username} side={<ExternalLink className="size-4" aria-hidden />} chevron={false} onClick={() => openLink(`https://t.me/${channel.username}`)} testId={`channel-${channel.country}`} />
          ))}
        </Group>
      )}
    </AppSheet>
  )
}

// C15.1. A failed count leaves the screen standing: `counts` is null and the numbers go.
export function AccountScreen() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const me = useMe()
  const guide = useGuide()
  const [channels, setChannels] = useState(false)
  const [prohibited, setProhibited] = useState(false)
  const prefs = me.data?.prefs
  // The tabs answer at once, and go back if the save fails.
  const [choice, setChoice] = useState<Partial<Prefs>>({})
  useEffect(() => track('courier.c15.account'), [])

  const save = useMutation({
    mutationFn: (body: Partial<Prefs>) => unwrap(patchJson<Prefs>('me/prefs/', body)),
    onSuccess: (saved, body) => {
      client.setQueryData<Me>(ME_KEY, (current) => (current ? { ...current, prefs: saved } : current))
      setChoice({})
      if (body.locale) showToast({ message: 'زبان عوض شد', icon: 'check' })
      track('courier.c15.prefs', { keys: Object.keys(body) })
    },
    onError: (_error, body) => {
      track('courier.c15.pref_save_failed')
      setChoice({})
      showToast({ message: 'ذخیره نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => pick(body) } })
      if (body.theme !== undefined) applyTheme(prefs?.theme)
    },
  })

  const pick = (body: Partial<Prefs>) => {
    setChoice(body)
    save.mutate(body)
  }

  if (me.isLoading) {
    return (
      <Page tab="me" testId="account-loading">
        <Stack center gap="gap-3">
          <Skeleton className="h-8 w-[50%]" />
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-24 w-full" />
        </Stack>
      </Page>
    )
  }
  if (!me.data) return <Page tab="me">{failedScreen(me.error, () => void me.refetch())}</Page>

  const counts = me.data.counts
  const shownPrefs = { ...prefs, ...choice }
  const locale = shownPrefs.locale === 'en' ? 'en' : 'fa'
  const theme: Theme = shownPrefs.theme === 'light' || shownPrefs.theme === 'dark' ? shownPrefs.theme : 'auto'
  return (
    <Page tab="me" testId="account">
      <Stack center gap="gap-3">
        <H1>حساب کاربری</H1>
        <Group>
          <Row icon={List} title="درخواست‌ها و سفرهای من" value={counts ? fa(counts.active_requests) : undefined} onClick={() => navigate('/me/requests')} testId="account-requests" />
          <Row
            icon={Users}
            title="هم‌مسیرها"
            value={counts ? fa(counts.open_matches) : undefined}
            side={counts?.pending_intros ? <Badge variant="destructive" data-testid="account-pending">{fa(counts.pending_intros)}</Badge> : undefined}
            onClick={() => navigate('/me/matches')}
            testId="account-matches"
          />
          <Row icon={Bell} title="گوش به زنگ مسیر" value={counts ? `${fa(counts.alerts)} از ۵` : undefined} onClick={() => navigate('/alerts')} testId="account-alerts" />
        </Group>
        <Card className="!gap-3 !p-4">
          <div className="flex items-center justify-between gap-2">
            <b className="text-sm">زبان</b>
            <div className="w-[140px]" data-testid="account-locale">
              <Segmented
                label="زبان"
                value={locale}
                onChange={(next) => pick({ locale: next })}
                options={[
                  { value: 'fa', label: 'FA' },
                  { value: 'en', label: 'EN' },
                ]}
              />
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <b className="text-sm">ظاهر</b>
            <div className="w-[200px]" data-testid="account-theme">
              <Segmented
                label="ظاهر"
                value={theme}
                onChange={(next) => {
                  applyTheme(next === 'auto' ? '' : next)
                  pick({ theme: next === 'auto' ? '' : next })
                }}
                options={[
                  { value: 'auto', label: 'خودکار' },
                  { value: 'light', label: 'روشن' },
                  { value: 'dark', label: 'تیره' },
                ]}
              />
            </div>
          </div>
        </Card>
        <Group>
          <Row icon={LifeBuoy} title="پشتیبانی" onClick={() => navigate('/me/support')} testId="account-support" />
          <Row icon={Info} title="راهنما" onClick={() => navigate('/guide')} testId="account-guide" />
          <Row icon={TriangleAlert} title="چه چیزهایی ممنوع است؟" onClick={() => setProhibited(true)} testId="account-prohibited" />
        </Group>
        <div className="grid grid-cols-2 gap-3">
          <Button variant="outline" className="w-full" onClick={() => setChannels(true)} data-testid="account-channels">
            <Megaphone />
            کانال
          </Button>
          <Button variant="outline" className="w-full" disabled={!guide.data?.bot_username} onClick={() => openLink(`https://t.me/${guide.data!.bot_username}`)} data-testid="account-bot">
            <Bot />
            ربات
          </Button>
        </div>
      </Stack>
      <ChannelsSheet open={channels} onClose={() => setChannels(false)} />
      <ProhibitedSheet open={prohibited} onClose={() => setProhibited(false)} />
    </Page>
  )
}

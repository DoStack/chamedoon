import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bell, Bot, BookOpen, ChevronLeft, ExternalLink, Handshake, LifeBuoy, List, Megaphone, TriangleAlert, type LucideIcon } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { useFeature } from './access'
import { patchJson, unwrap } from './api'
import { ProhibitedSheet, useGuide } from './guide'
import { applyTheme, H1, ME_KEY, Page, Stack, useMe } from './page'
import { Segmented } from './parts'
import { failedScreen, openLink } from './states'
import type { Me } from './types'

type Prefs = Me['prefs']
type Theme = 'auto' | 'light' | 'dark'

// Design v4 `row`: an icon in a soft square, the title, the value or count, a chevron.
function Row({ icon: Icon, title, value, side, onClick, testId, chevron = true }: { icon: LucideIcon; title: string; value?: string; side?: ReactNode; onClick: () => void; testId: string; chevron?: boolean }) {
  return (
    <button type="button" className="flex w-full items-center gap-3 border-b px-3.5 py-3 text-start text-sm font-semibold last:border-b-0" onClick={onClick} data-testid={testId}>
      <span className="flex size-[34px] flex-none items-center justify-center rounded-[10px] bg-muted">
        <Icon className="size-[18px]" aria-hidden />
      </span>
      <span className="min-w-0 flex-1 truncate">{title}</span>
      <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
        {side}
        {value ? <span>{value}</span> : null}
        {chevron ? <ChevronLeft className="size-4" aria-hidden /> : null}
      </span>
    </button>
  )
}

function Group({ children }: { children: ReactNode }) {
  return <Card className="!gap-0 !rounded-2xl !p-0">{children}</Card>
}

function Count({ value, testId }: { value: number; testId?: string }) {
  return (
    <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-[11px] font-bold text-primary-foreground" data-testid={testId}>
      {fa(value)}
    </span>
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
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              track('courier_retry_tap', { where: 'channels' })
              void guide.refetch()
            }}
          >
            تلاش مجدد
          </Button>
        </div>
      ) : (
        <Group>
          {guide.data?.main_channel ? <Row icon={Megaphone} title="کانال اصلی چمدون" side={<ExternalLink className="size-4" aria-hidden />} chevron={false} onClick={() => (track('courier_account_channel_tap', { channel: 'main' }), openLink(`https://t.me/${guide.data!.main_channel}`))} testId="channel-main" /> : null}
          {channels.map((channel) => (
            <Row key={channel.username} icon={Megaphone} title={channel.title || channel.username} side={<ExternalLink className="size-4" aria-hidden />} chevron={false} onClick={() => (track('courier_account_channel_tap', { channel: 'country', country: channel.country }), openLink(`https://t.me/${channel.username}`))} testId={`channel-${channel.country}`} />
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
  const connections = useFeature('v4_connections')
  const prefs = me.data?.prefs
  // The tabs answer at once, and go back if the save fails.
  const [choice, setChoice] = useState<Partial<Prefs>>({})
  useEffect(() => track('courier.c15.account'), [])
  useEffect(() => {
    if (me.data) track('courier_account_view', { active_requests: me.data.counts?.active_requests ?? null, open_matches: me.data.counts?.open_matches ?? null, alerts: me.data.counts?.alerts ?? null, pending_intros: me.data.counts?.pending_intros ?? null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(me.data)])

  const save = useMutation({
    mutationFn: (body: Partial<Prefs>) => unwrap(patchJson<Prefs>('me/prefs/', body)),
    onSuccess: (saved, body) => {
      client.setQueryData<Me>(ME_KEY, (current) => (current ? { ...current, prefs: saved } : current))
      setChoice({})
      if (body.locale) showToast({ message: 'زبان عوض شد', icon: 'check' })
      track('courier.c15.prefs', { keys: Object.keys(body) })
      track('courier_prefs_saved', { keys: Object.keys(body) })
    },
    onError: (_error, body) => {
      track('courier.c15.pref_save_failed')
      track('courier_prefs_save_failed', { surface: 'account', keys: Object.keys(body) })
      setChoice({})
      showToast({ message: 'ذخیره نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'prefs' }), pick(body)) } })
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
  const theme: Theme = shownPrefs.theme === 'light' || shownPrefs.theme === 'dark' ? shownPrefs.theme : 'auto'
  return (
    <Page tab="me" testId="account">
      <Stack center gap="gap-3">
        <H1>حساب کاربری</H1>
        <Card className="!flex-row !items-center !gap-3 !rounded-2xl !p-3.5" data-testid="account-profile">
          <span className="flex size-[52px] flex-none items-center justify-center rounded-full bg-accent text-xl font-extrabold text-accent-foreground">{(me.data.first_name || '؟').slice(0, 1)}</span>
          <b className="ph-no-capture min-w-0 truncate text-[17px]">
            <bdi>{me.data.first_name}</bdi>
          </b>
        </Card>
        <Group>
          <Row icon={List} title="درخواست‌ها و سفرهای من" side={counts?.active_requests ? <Count value={counts.active_requests} /> : undefined} onClick={() => (track('courier_account_row_tap', { row: 'requests' }), navigate('/me/requests'))} testId="account-requests" />
          <Row
            icon={Handshake}
            title={connections ? 'ارتباط‌ها' : 'هم‌مسیرها'}
            value={counts && !counts.pending_intros ? fa(counts.open_matches) : undefined}
            side={counts?.pending_intros ? <Count value={counts.pending_intros} testId="account-pending" /> : undefined}
            onClick={() => (track('courier_account_row_tap', { row: 'matches' }), navigate('/me/matches'))}
            testId="account-matches"
          />
          <Row icon={Bell} title="گوش به زنگ" value={counts ? `${fa(counts.alerts)} از ۵` : undefined} onClick={() => (track('courier_account_row_tap', { row: 'alerts' }), navigate('/alerts'))} testId="account-alerts" />
        </Group>
        <Card className="!gap-3 !p-4">
          <div className="flex items-center justify-between gap-2">
            <b className="text-sm">ظاهر</b>
            <div className="w-[200px]" data-testid="account-theme">
              <Segmented
                label="ظاهر"
                value={theme}
                onChange={(next) => {
                  track('courier_prefs_change', { key: 'theme', value: next })
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
          <Row icon={LifeBuoy} title="پشتیبانی" onClick={() => (track('courier_account_row_tap', { row: 'support' }), navigate('/me/support'))} testId="account-support" />
          <Row icon={BookOpen} title="راهنما" onClick={() => (track('courier_account_row_tap', { row: 'guide' }), navigate('/guide'))} testId="account-guide" />
          <Row icon={TriangleAlert} title="چه چیزهایی ممنوع است؟" onClick={() => (track('courier_account_row_tap', { row: 'prohibited' }), setProhibited(true))} testId="account-prohibited" />
        </Group>
        <div className="grid grid-cols-2 gap-3">
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              track('courier_account_row_tap', { row: 'channels' })
              setChannels(true)
            }}
            data-testid="account-channels"
          >
            <Megaphone />
            کانال‌ها
          </Button>
          <Button
            variant="outline"
            className="w-full"
            disabled={!guide.data?.bot_username}
            onClick={() => {
              track('courier_account_row_tap', { row: 'bot' })
              openLink(`https://t.me/${guide.data!.bot_username}`)
            }}
            data-testid="account-bot"
          >
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

import { useQuery } from '@tanstack/react-query'
import { Bell, Bookmark, Bot, ChevronLeft, ChevronRight, CircleAlert, FileText, Languages, LifeBuoy, MapPin, Megaphone, RefreshCw, ShieldCheck, SunMoon, type LucideIcon } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Account } from '@/api/types'
import { saveFailedToast } from '@/components/app-toast'
import { MainNav } from '@/components/main-nav'
import { PRIVACY_TEXT } from '@/components/privacy-note'
import { Segmented } from '@/components/segmented'
import { StateBox } from '@/components/state-screen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ACCOUNT_KEY, fetchAccount } from '@/lib/account'
import { cityFromPrefs, saveCity, useSavedCity, type SavedCity } from '@/lib/city'
import { useFeature } from '@/lib/features'
import { usePosting } from '@/lib/posting'
import { cityLabel, fa } from '@/lib/format'
import { legacyHomeUrl, openExternal, SUPPORT_URL } from '@/lib/links'
import { appDir, t } from '@/lib/locale'
import { queryClient } from '@/lib/query'
import { track } from '@/lib/report'
import { adoptServerUi, chooseUi, useUiPrefs, type UiPrefs } from '@/lib/ui-prefs'
import { useCities } from '@/lib/use-cities'
import { CitySheet } from './city-sheet'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

export function AccountRow({ icon: Icon, title, value, onClick, testId }: { icon: LucideIcon; title: string; value?: string; onClick: () => void; testId?: string }) {
  const Chevron = appDir() === 'rtl' ? ChevronLeft : ChevronRight
  return (
    <button type="button" onClick={() => { track('account_row', { row: testId }); onClick() }} data-testid={testId} className="flex min-h-[52px] w-full items-center gap-3 py-2 text-start outline-none focus-visible:ring-3 focus-visible:ring-ring/50">
      <Icon className="size-[18px] flex-none" aria-hidden />
      <span className="min-w-0 flex-1 text-sm font-medium">{title}</span>
      {value ? <span className="min-w-0 truncate text-[13px] text-muted-foreground">{value}</span> : null}
      <Chevron className="size-4 flex-none text-muted-foreground" aria-hidden />
    </button>
  )
}

function Group({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <Card className="gap-0 divide-y divide-border px-4 py-1" data-testid={testId}>
      {children}
    </Card>
  )
}

// F22: applied on tap, with no save button.
function Control<T extends string>({ icon: Icon, label, value, options, onChange, testId }: { icon: LucideIcon; label: string; value: T; options: Array<{ value: T; label: string }>; onChange: (next: T) => void; testId: string }) {
  return (
    <div className="flex min-h-[52px] items-center justify-between gap-2 py-2">
      <span className="flex min-w-0 items-center gap-3">
        <Icon className="size-[18px] flex-none" aria-hidden />
        <span className="text-sm font-medium">{label}</span>
      </span>
      <div className="w-[196px] flex-none">
        <Segmented testId={testId} label={label} value={value} options={options} onChange={onChange} />
      </div>
    </div>
  )
}

const hour = (time: string) => fa(Number(time.slice(0, 2)))

function notifValue({ prefs }: Account) {
  if (!prefs.notify_enabled) return t('خاموش')
  return prefs.quiet_from && prefs.quiet_to ? t('بی‌صدا {from} تا {to}', { from: hour(prefs.quiet_from), to: hour(prefs.quiet_to) }) : t('روشن')
}

function savedValue(account: Account, alerts: boolean) {
  const ads = t('{n} آگهی', { n: fa(account.counts.saved) })
  return alerts ? t('{n} گوش به زنگ · {ads}', { n: fa(account.counts.alerts_active), ads }) : ads
}

function AccountBody({ account }: { account: Account }) {
  const navigate = useNavigate()
  const alerts = useFeature('alerts')
  const posting = usePosting()
  const cities = useCities()
  const saved = useSavedCity()
  const city: SavedCity | null = saved ?? (cities.data ? cityFromPrefs(account.prefs, cities.data) : null)
  const [picking, setPicking] = useState(false)
  const username = account.user.telegram_username
  const unread = account.counts.unread
  const ui = useUiPrefs()
  const choose = (change: Partial<UiPrefs>) => {
    track('locale' in change ? 'locale_changed' : 'theme_changed', change)
    void chooseUi(change).then((saved) => {
      if (saved) return
      track('prefs_failed')
      saveFailedToast(() => choose(change), 'prefs')
    })
  }
  useEffect(() => void adoptServerUi(account.prefs), [account.prefs])
  const pick = (next: SavedCity) => {
    track('city_changed', { city: next.slug })
    void saveCity(next).then((ok) => {
      if (ok) return void queryClient.invalidateQueries({ queryKey: ACCOUNT_KEY })
      track('prefs_failed')
      saveFailedToast(() => pick(next), 'prefs')
    })
  }
  const open = (url: string, event: string) => {
    track(event)
    openExternal(url)
  }
  return (
    <>
      <div className="flex items-center justify-between gap-2" data-testid="account-who">
        <div className="min-w-0">
          <h2 dir="auto" className="ph-no-capture m-0 truncate text-lg leading-[26px] font-extrabold">
            {account.user.first_name}
          </h2>
          {username ? (
            <div className="ph-no-capture text-[13px] text-muted-foreground" data-testid="account-handle">
              <bdi dir="ltr">@{username}</bdi>
            </div>
          ) : null}
        </div>
        {/* F17#1: the bell counts every notification. */}
        <span className="relative inline-flex flex-none">
          <Button variant="ghost" size="icon" data-testid="bell" aria-label={unread ? t('اعلان‌ها، {n} تازه', { n: fa(unread) }) : t('اعلان‌ها')} onClick={() => { track('inbox_tap'); navigate('/inbox') }}>
            <Bell className="size-5" />
          </Button>
          {unread ? (
            <Badge data-testid="bell-count" className="pointer-events-none absolute -top-1 -end-1 h-4 min-w-4 px-1 text-[10px]">
              {fa(unread)}
            </Badge>
          ) : null}
        </span>
      </div>
      <Group testId="account-settings">
        <AccountRow icon={MapPin} title={t('شهر من')} value={city ? cityLabel(city) : undefined} testId="row-city" onClick={() => (city ? setPicking(true) : navigate('/choose'))} />
        <Control
          icon={Languages}
          label={t('زبان')}
          testId="control-language"
          value={ui.locale}
          onChange={(locale) => choose({ locale })}
          options={[
            { value: 'fa', label: 'FA' },
            { value: 'en', label: 'EN' },
            { value: 'de', label: 'DE' },
          ]}
        />
        <Control
          icon={SunMoon}
          label={t('ظاهر')}
          testId="control-theme"
          value={ui.theme}
          onChange={(theme) => choose({ theme })}
          options={[
            { value: 'light', label: t('روشن') },
            { value: 'dark', label: t('تاریک') },
            { value: 'auto', label: t('خودکار') },
          ]}
        />
        <AccountRow icon={Bell} title={t('اعلان‌ها')} value={notifValue(account)} testId="row-notifications" onClick={() => navigate('/settings/notifications')} />
      </Group>
      <Group testId="account-mine">
        <AccountRow icon={FileText} title={t('آگهی‌های من')} value={t('{n} آگهی', { n: fa(account.counts.listings) })} testId="row-mine" onClick={() => (posting ? navigate('/mine') : location.assign(legacyHomeUrl('mine/')))} />
        <AccountRow icon={Bookmark} title={t('نشان‌شده‌ها')} value={savedValue(account, alerts)} testId="row-saved" onClick={() => navigate('/saved')} />
      </Group>
      <Group testId="account-help">
        {city ? <AccountRow icon={ShieldCheck} title={t('راهنمای ایمنی {city}', { city: cityLabel(city) })} testId="row-safety" onClick={() => navigate('/safety')} /> : null}
        <AccountRow icon={LifeBuoy} title={t('پشتیبانی')} testId="row-support" onClick={() => open(SUPPORT_URL, 'support_open')} />
      </Group>
      <div className="flex gap-2">
        {account.channel ? (
          <Button variant="outline" className="min-w-0 flex-1" data-testid="open-channel" onClick={() => open(`https://t.me/${account.channel!.username}`, 'channel_open')}>
            <Megaphone />
            <span className="truncate">{city ? t('کانال {city}', { city: cityLabel(city) }) : account.channel.title}</span>
          </Button>
        ) : null}
        <Button variant="outline" className="min-w-0 flex-1" data-testid="open-bot" onClick={() => open(account.bot_url, 'bot_open')}>
          <Bot />
          <span className="truncate">{t('ربات خانه')}</span>
        </Button>
      </div>
      <p className="m-0 mt-auto text-xs leading-5 text-muted-foreground" data-testid="account-privacy">{t(PRIVACY_TEXT)}</p>
      {city && picking ? <CitySheet open current={city} onClose={() => setPicking(false)} onPick={pick} /> : null}
    </>
  )
}

// F21. Keeps the tab in every state, as the flow draws it.
export default function AccountScreen() {
  const online = useOnline()
  const query = useQuery({ queryKey: ACCOUNT_KEY, queryFn: fetchAccount, staleTime: 0 })
  const slow = useAfter(3000, query.isPending)
  useEffect(() => {
    if (query.data) track('account_view')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(query.data)])
  useEffect(() => {
    if (query.isError && !query.data) track('account_failed')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.isError])
  let body: ReactNode
  if (query.isPending) {
    body = (
      <div className="flex flex-col gap-3" data-testid="account-loading">
        {slow ? <SlowNote /> : null}
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  } else if (!query.data) {
    // F21#9
    body = (
      <div className="flex min-h-[420px] flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title={t('اطلاعات حساب بارگذاری نشد')} testId="account-failed">
          <Button className="w-full" onClick={() => { track('retry_tap', { screen: 'account' }); void query.refetch() }}>
            <RefreshCw />
            {t('تلاش مجدد')}
          </Button>
        </StateBox>
      </div>
    )
  } else {
    body = <AccountBody account={query.data} />
  }
  return (
    <div className="flex h-full flex-col" data-testid="account-screen">
      <MainNav place="top" active="me" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-lg flex-col gap-3 px-4 pt-3 pb-4 md:px-6">
          {online ? null : <OfflineBar onRetry={() => void query.refetch()} />}
          {body}
        </div>
      </div>
      <MainNav place="bottom" active="me" />
    </div>
  )
}

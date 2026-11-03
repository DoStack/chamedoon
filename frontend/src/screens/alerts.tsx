import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { Bell, BellOff, BellRing, Bot, ChevronLeft, ChevronRight, CircleAlert, EllipsisVertical, Pause, Pencil, Plus, RefreshCw, Trash, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import type { AlertList, AlertRow, Prefs } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
import { goToList, MainNav } from '@/components/main-nav'
import { Segmented } from '@/components/segmented'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { ALERTS_KEY, alertsFetch, fetchAlerts } from '@/lib/alerts'
import { filtersOfView, filterChips, rowName, viewOfFilters } from '@/lib/alert-view'
import { ApiFailureError, meFetch, type ApiFailure } from '@/lib/api'
import { useAlertEntry } from '@/lib/features'
import { useBack } from '@/lib/back'
import { fa, formatDate } from '@/lib/format'
import type { View } from '@/lib/listing-view'
import { appDir, t } from '@/lib/locale'
import { openExternal } from '@/lib/links'
import { queryClient } from '@/lib/query'
import { track } from '@/lib/report'
import { useCities } from '@/lib/use-cities'
import { cn } from '@/lib/utils'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'
import { savedQueryOptions, SavedScreen } from './saved'
import { AlertFilterSheet } from './alert-filter-sheet'

type Delivery = AlertRow['delivery']

export const NOW_LABEL = 'به‌محض پیدا شدن'

const readList = () => queryClient.getQueryData<AlertList>(ALERTS_KEY)
const writeList = (change: (list: AlertList) => AlertList) => {
  const list = readList()
  if (list) queryClient.setQueryData(ALERTS_KEY, change(list))
}
const replaceRow = (row: AlertRow) => writeList((list) => ({ ...list, results: list.results.map((item) => (item.id === row.id ? row : item)) }))
const refresh = () => void queryClient.invalidateQueries({ queryKey: ALERTS_KEY })

const patch = (id: number, body: object) => alertsFetch<AlertRow>(`/api/v1/me/alerts/${id}/`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

// What the whole app already answers (maintenance, session over), and what closes the screen (feature_off), need no toast.
export const QUIET = new Set(['maintenance', 'user_blocked', 'session_expired', 'auth_invalid', 'auth_outside', 'miniapp_off', 'feature_off'])

function writeFailed(failure: ApiFailure, retry: () => void) {
  track('alert_write_failed', { code: failure.code })
  if (QUIET.has(failure.code)) return
  if (failure.code === 'alert_gone') {
    refresh()
    return void showToast({ id: 'alert', message: t('این گوش به زنگ دیگر نیست'), icon: 'alert' })
  }
  if (failure.code === 'rate_limited') return rateLimitedToast(retry, 'alert')
  saveFailedToast(retry, 'alert') // F25#10
}

export function CapSheet({ cap, onManage, onClose }: { cap: number; onManage: () => void; onClose: () => void }) {
  return (
    <AppSheet open onClose={null} onBack={onClose} label={t('سقف گوش به زنگ')} testId="alert-cap">
      <StateBox icon={BellRing} title={t('سقف {n} گوش به زنگ پر شده است', { n: fa(cap) })} />
      <div className="flex flex-col gap-2">
        <Button className="w-full" onClick={onManage}>
          {t('مدیریت گوش به زنگ‌ها')}
        </Button>
        <Button variant="outline" className="w-full" onClick={onClose}>
          {t('بستن')}
        </Button>
      </div>
    </AppSheet>
  )
}

export function DuplicateSheet({ row, onSee, onClose }: { row: AlertRow; onSee: () => void; onClose: () => void }) {
  const cities = useCities()
  const name = rowName(row, cities.data)
  return (
    <AppSheet open onClose={null} onBack={onClose} label={t('گوش به زنگ تکراری')} testId="alert-duplicate">
      <StateBox icon={BellRing} title={t('این گوش به زنگ را از قبل دارید')} />
      <Card className="gap-1 p-4">
        <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">{name.title}</h3>
        <p className="m-0 text-[13px] text-muted-foreground">
          {name.sub} · {row.delivery === 'now' ? t(NOW_LABEL) : t('روزانه')}
        </p>
      </Card>
      <div className="flex flex-col gap-2">
        <Button className="w-full" onClick={onSee}>
          {t('دیدن گوش به زنگ')}
        </Button>
        <Button variant="outline" className="w-full" onClick={onClose}>
          {t('بستن')}
        </Button>
      </div>
    </AppSheet>
  )
}

// F16#3. «فیلتر» opens F06 for this alert's city. The filters only reach the server on «ذخیره».
function EditSheet({ row, onDelete, onClose, onDuplicate }: { row: AlertRow; onDelete: () => void; onClose: () => void; onDuplicate: (id: number) => void }) {
  const [view, setView] = useState<View>(() => viewOfFilters(row.filters))
  const [delivery, setDelivery] = useState<Delivery>(row.delivery)
  const [sending, setSending] = useState(false)
  const [picking, setPicking] = useState(false)
  const chips = filterChips(view, row.country)
  const save = async () => {
    setSending(true)
    const result = await patch(row.id, { filters: filtersOfView(view), ...(row.alert_state === 'active' ? { delivery } : {}) })
    setSending(false)
    if (result.ok) {
      track('alert_edited')
      replaceRow(result.data)
      refresh()
      return onClose()
    }
    if (result.code === 'alert_duplicate' && result.duplicateId) return onDuplicate(result.duplicateId)
    writeFailed(result, () => void save())
  }
  const remove = (chip: (typeof chips)[number]) => {
    const next = chip.clear(view)
    // The server adds an area id's name, so taking the name off takes its id too.
    setView(chip.id.startsWith('area-') ? { ...next, area_ids: [] } : next)
  }
  if (picking) return <AlertFilterSheet row={row} view={view} onClose={() => setPicking(false)} onApply={setView} />
  return (
    <AppSheet
      open
      onClose={onClose}
      label={t('ویرایش گوش به زنگ')}
      title={t('ویرایش گوش به زنگ')}
      testId="alert-edit"
      foot={
        <div className="flex flex-col gap-2">
          <Button className="w-full" disabled={sending} onClick={() => void save()}>
            {sending ? <Spinner /> : null}
            {t('ذخیره')}
          </Button>
          <Button variant="ghost" className="w-full text-destructive" onClick={onDelete}>
            {t('حذف گوش به زنگ')}
          </Button>
        </div>
      }
    >
      <section className="flex flex-col gap-2">
        <h3 className="m-0 text-sm font-bold">{t('فیلترها')}</h3>
        <div className="flex flex-wrap gap-2" data-testid="edit-chips">
          {chips.map((chip) => (
            <Button key={chip.id} variant="secondary" size="sm" onClick={() => remove(chip)}>
              {chip.label}
              <X />
            </Button>
          ))}
          <Button variant="outline" size="sm" className="border-dashed" onClick={() => setPicking(true)}>
            <Plus />
            {t('فیلتر')}
          </Button>
        </div>
      </section>
      {row.alert_state === 'active' ? (
        <section className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-bold">{t('ارسال')}</h3>
          <Segmented
            label={t('ارسال')}
            value={delivery}
            onChange={setDelivery}
            options={[
              { value: 'now', label: t(NOW_LABEL) },
              { value: 'daily', label: t('روزانه، ساعت ۹') },
            ]}
          />
        </section>
      ) : null}
    </AppSheet>
  )
}

// F16#5: deleting always asks.
function DeleteConfirm({ title, onConfirm, onClose }: { title: string; onConfirm: () => void; onClose: () => void }) {
  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent dir={appDir()} data-testid="alert-delete">
        <AlertDialogHeader>
          <AlertDialogTitle>{t('این گوش به زنگ حذف شود؟')}</AlertDialogTitle>
          <AlertDialogDescription>{title}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogAction variant="destructive" onClick={onConfirm}>
            <Trash />
            {t('حذف')}
          </AlertDialogAction>
          <AlertDialogCancel variant="outline">{t('انصراف')}</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

type CardActions = { edit: () => void; ask: () => void; state: (next: AlertRow['alert_state']) => void; delivery: (next: Delivery) => void; open: () => void }

const MENU_WIDTH = 200
const MENU_ROW = 44

// `menu` in the design library. Placed by hand, in the viewport, so the scroller around the cards can never cut it off.
function AlertMenu({ row, on }: { row: AlertRow; on: CardActions }) {
  const wanted = row.origin === 'wanted'
  const gone = !row.city_active
  const state = row.alert_state
  const [spot, setSpot] = useState<React.CSSProperties | null>(null)
  const close = () => setSpot(null)
  const items: Array<{ icon: typeof Bell; label: string; run: () => void; danger?: boolean }> = [
    ...(!wanted && !gone ? [{ icon: Pencil, label: t('ویرایش'), run: on.edit }] : []),
    ...(!wanted && !gone && state === 'active' ? [{ icon: Pause, label: t('مکث ۱ هفته'), run: () => on.state('paused') }] : []),
    ...(!wanted && !gone && state === 'paused' ? [{ icon: Bell, label: t('ادامه'), run: () => on.state('active') }] : []),
    ...(!gone && state === 'off' ? [{ icon: Bell, label: t('روشن کردن'), run: () => on.state('active') }] : []),
    ...(!gone && state !== 'off' ? [{ icon: BellOff, label: t('خاموش'), run: () => on.state('off') }] : []),
    ...(!wanted ? [{ icon: Trash, label: t('حذف'), run: on.ask, danger: true }] : []),
  ]
  const open = (from: HTMLElement) => {
    const box = from.getBoundingClientRect()
    const left = Math.max(8, Math.min(appDir() === 'rtl' ? box.left : box.right - MENU_WIDTH, window.innerWidth - MENU_WIDTH - 8))
    const height = items.length * MENU_ROW + 8
    const below = box.bottom + 4 + height <= window.innerHeight - 8
    setSpot(below ? { left, top: box.bottom + 4, width: MENU_WIDTH } : { left, bottom: window.innerHeight - box.top + 4, width: MENU_WIDTH })
  }
  useBack(spot ? close : null)
  useEffect(() => {
    if (!spot) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [spot])
  return (
    <>
      <Button variant="ghost" size="icon" aria-label={t('گزینه‌ها')} aria-haspopup="menu" aria-expanded={Boolean(spot)} data-testid="alert-menu" onClick={(event) => open(event.currentTarget)}>
        <EllipsisVertical className="size-5" />
      </Button>
      {spot ? (
        <>
          <div className="fixed inset-0 z-40" data-testid="alert-menu-scrim" onClick={close} />
          <div role="menu" aria-label={t('گزینه‌ها')} dir={appDir()} style={spot} className="fixed z-50 rounded-xl bg-popover p-1 text-popover-foreground shadow-lg ring-1 ring-border">
            {items.map((item) => (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                className={cn('flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-start text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none', item.danger && 'text-destructive')}
                onClick={() => {
                  close()
                  item.run()
                }}
              >
                <item.icon className="size-4 flex-none" aria-hidden />
                {item.label}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </>
  )
}

function AlertCard({ row, title, sub, on }: { row: AlertRow; title: string; sub: string; on: CardActions }) {
  const gone = !row.city_active
  const wanted = row.origin === 'wanted'
  const active = row.alert_state === 'active' && !gone && !wanted
  const Chevron = appDir() === 'rtl' ? ChevronLeft : ChevronRight
  return (
    <Card className="gap-3 p-4" data-testid="alert-card" data-state={row.alert_state}>
      <div className="flex items-start gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">{title}</h3>
          <p className="m-0 text-[13px] text-muted-foreground">{row.alert_state === 'off' && !gone && !wanted ? t('خاموش · پیامی نمی‌آید') : sub}</p>
          {row.alert_state === 'paused' && row.paused_until ? (
            <Badge variant="outline" className="mt-1">
              <Pause />
              {t('مکث تا {date}', { date: formatDate(row.paused_until) })}
            </Badge>
          ) : null}
          {wanted ? (
            <Badge variant="outline" className="mt-1">
              {t('از آگهی دنبال خانه‌ی شما')}
            </Badge>
          ) : null}
          {gone ? (
            <Badge variant="outline" className="mt-1">
              {t('این شهر دیگر در چمدون نیست')}
            </Badge>
          ) : null}
        </div>
        <AlertMenu row={row} on={on} />
      </div>
      {active ? (
        <div className="flex items-center gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap gap-2" data-testid="alert-delivery">
            {(['now', 'daily'] as const).map((value) => (
              <Button key={value} size="sm" variant={row.delivery === value ? 'default' : 'outline'} aria-pressed={row.delivery === value} onClick={() => row.delivery !== value && on.delivery(value)}>
                {value === 'now' ? t(NOW_LABEL) : t('روزانه')}
              </Button>
            ))}
          </div>
          {row.new_count > 0 ? (
            <Button variant="ghost" size="sm" data-testid="alert-new" onClick={on.open}>
              {t('{n} جدید', { n: fa(row.new_count) })}
              <Chevron />
            </Button>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

function useActions() {
  const [cap, setCap] = useState(false)

  const setState = (row: AlertRow, next: AlertRow['alert_state']) => {
    const before = row
    replaceRow({ ...row, alert_state: next, paused_until: next === 'active' ? null : row.paused_until, new_count: next === 'active' ? row.new_count : 0 })
    void patch(row.id, { alert_state: next }).then((result) => {
      if (!result.ok) {
        replaceRow(before)
        if (result.code === 'alert_cap') {
          track('alert_capped')
          return setCap(true) // F16#10
        }
        return writeFailed(result, () => setState(before, next))
      }
      replaceRow(result.data)
      refresh()
      if (next === 'paused') {
        // F16#4
        track('alert_paused')
        showToast({
          id: 'alert',
          message: t('گوش به زنگ تا {date} مکث شد', { date: formatDate(result.data.paused_until ?? '') }),
          icon: 'info',
          action: { label: t('برگردان'), onClick: () => setState(result.data, 'active') },
        })
      } else if (next === 'active') {
        track('alert_resumed')
        showToast({ id: 'alert', message: t('گوش به زنگ دوباره روشن شد'), icon: 'check' })
      }
    })
  }

  const setDelivery = (row: AlertRow, delivery: Delivery) => {
    replaceRow({ ...row, delivery })
    void patch(row.id, { delivery }).then((result) => {
      if (result.ok) return
      replaceRow(row)
      writeFailed(result, () => setDelivery(row, delivery))
    })
  }

  const remove = (row: AlertRow) => {
    const list = readList()
    writeList((current) => ({ ...current, results: current.results.filter((item) => item.id !== row.id), active_count: current.active_count - (row.alert_state === 'off' || row.origin === 'wanted' ? 0 : 1) }))
    void alertsFetch<null>(`/api/v1/me/alerts/${row.id}/`, { method: 'DELETE' }).then((result) => {
      if (result.ok) {
        track('alert_deleted')
        return refresh()
      }
      if (list) queryClient.setQueryData(ALERTS_KEY, list)
      writeFailed(result, () => remove(row))
    })
  }

  return { cap, closeCap: () => setCap(false), setState, setDelivery, remove }
}

const turnNotificationsOn = async () => {
  const result = await meFetch<Prefs>('/api/v1/me/prefs/', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ notify_enabled: true }) })
  if (!result.ok) return saveFailedToast(() => void turnNotificationsOn(), 'alert')
  track('notif_on')
  writeList((list) => ({ ...list, notify_enabled: true }))
}

function AlertsBody({ list }: { list: AlertList }) {
  const navigate = useNavigate()
  const cities = useCities()
  const canCreate = useAlertEntry()
  const actions = useActions()
  const [editing, setEditing] = useState<AlertRow | null>(null)
  const [deleting, setDeleting] = useState<AlertRow | null>(null)
  const [duplicate, setDuplicate] = useState<AlertRow | null>(null)
  const rows = list.results
  const newButton = () => (
    <Button variant={rows.length ? 'outline' : 'default'} className={rows.length ? 'w-full' : undefined} onClick={() => goToList()} data-testid="alert-new-button">
      <Plus />
      {t('گوش به زنگ جدید')}
    </Button>
  )
  useEffect(() => {
    track('alerts_view', { count: rows.length })
    if (list.bot_blocked) track('alerts_blocked')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return (
    <>
      {list.notify_enabled ? null : (
        <Alert data-testid="notify-off">
          <BellOff />
          <AlertDescription className="flex w-full items-center justify-between gap-2">
            <span className="font-bold text-foreground">{t('اعلان‌ها خاموش است')}</span>
            <Button size="sm" onClick={() => void turnNotificationsOn()}>
              {t('روشن کردن')}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {list.bot_blocked ? (
        <Alert data-testid="bot-blocked">
          <Bot />
          <AlertDescription className="flex w-full items-center justify-between gap-2">
            <span className="text-foreground">{t('ربات بسته است و پیامی نمی‌رسد.')}</span>
            <Button size="sm" onClick={() => openExternal(list.bot_url)}>
              {t('باز کردن ربات')}
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {rows.length === 0 ? (
        // F16#7
        <div className="flex min-h-[420px] flex-1 items-center justify-center">
          <StateBox icon={BellOff} title={t('هنوز گوش به زنگی ندارید')} testId="alerts-empty">
            {canCreate ? newButton() : null}
          </StateBox>
        </div>
      ) : (
        <>
          {rows.map((row) => {
            const name = rowName(row, cities.data)
            return (
              <AlertCard
                key={row.id}
                row={row}
                title={name.title}
                sub={name.sub}
                on={{
                  edit: () => setEditing(row),
                  ask: () => setDeleting(row),
                  state: (next) => actions.setState(row, next),
                  delivery: (next) => actions.setDelivery(row, next),
                  open: () => navigate(`/alerts/${row.id}`),
                }}
              />
            )
          })}
          {canCreate ? newButton() : null}
        </>
      )}
      {editing ? (
        <EditSheet
          key={editing.id}
          row={editing}
          onClose={() => setEditing(null)}
          onDelete={() => {
            setDeleting(editing)
            setEditing(null)
          }}
          onDuplicate={(id) => {
            const same = rows.find((item) => item.id === id)
            setEditing(null)
            if (same) setDuplicate(same)
          }}
        />
      ) : null}
      {deleting ? (
        <DeleteConfirm
          title={rowName(deleting, cities.data).title}
          onClose={() => setDeleting(null)}
          onConfirm={() => {
            actions.remove(deleting)
            setDeleting(null)
          }}
        />
      ) : null}
      {duplicate ? <DuplicateSheet row={duplicate} onSee={() => setDuplicate(null)} onClose={() => setDuplicate(null)} /> : null}
      {actions.cap ? <CapSheet cap={list.cap} onManage={actions.closeCap} onClose={actions.closeCap} /> : null}
    </>
  )
}

function SavedHead({ tab, onTab, list }: { tab: 'alerts' | 'ads'; onTab: (next: 'alerts' | 'ads') => void; list?: AlertList }) {
  const saved = useInfiniteQuery(savedQueryOptions)
  const total = saved.data?.pages[0]?.total
  return (
    <div className="flex w-full flex-none flex-col gap-3">
      <h1 className="m-0 text-2xl leading-8 font-extrabold">{t('نشان‌شده‌ها')}</h1>
      <Segmented
        testId="saved-tabs"
        label={t('نشان‌شده‌ها')}
        value={tab}
        onChange={onTab}
        options={[
          { value: 'alerts', label: list ? t('گوش به زنگ ({n} از {cap})', { n: fa(list.active_count), cap: fa(list.cap) }) : t('گوش به زنگ') },
          { value: 'ads', label: total !== undefined ? t('آگهی‌ها ({n})', { n: fa(total) }) : t('آگهی‌ها') },
        ]}
      />
    </div>
  )
}

const OFFLINE = new Set(['offline', 'network_error', 'timeout'])

// F16 inside the saved tab. `?tab=ads` is F14 as it is, under the same head.
export default function AlertsHub() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'ads' ? 'ads' : 'alerts'
  const online = useOnline()
  const query = useQuery({ queryKey: ALERTS_KEY, queryFn: fetchAlerts, staleTime: 0 })
  const slow = useAfter(3000, query.isPending)
  const code = query.error instanceof ApiFailureError ? query.error.failure.code : null
  useEffect(() => {
    if (query.isError && !query.data) track(code && OFFLINE.has(code) ? 'alerts_offline' : 'alerts_failed', { code })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.isError])
  const onTab = (next: 'alerts' | 'ads') => setParams(next === 'ads' ? { tab: 'ads' } : {}, { replace: true })
  const head = <SavedHead tab={tab} onTab={onTab} list={query.data} />
  if (tab === 'ads') return <SavedScreen head={head} />
  let body: React.ReactNode
  if (query.isPending) {
    body = (
      <div className="flex flex-col gap-3" data-testid="alerts-loading">
        {slow ? <SlowNote /> : null}
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    )
  } else if (!query.data) {
    body = (
      <div className="flex min-h-[420px] flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title={t('بارگذاری نشد')} testId="alerts-failed">
          <Button className="w-full" onClick={() => void query.refetch()}>
            <RefreshCw />
            {t('تلاش مجدد')}
          </Button>
        </StateBox>
      </div>
    )
  } else {
    body = <AlertsBody list={query.data} />
  }
  return (
    <div className="flex h-full flex-col" data-testid="alerts-screen">
      <MainNav place="top" active="saved" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-lg flex-col gap-3 px-4 pt-3 pb-4 md:px-6">
          {online ? null : <OfflineBar onRetry={() => void query.refetch()} />}
          {head}
          {body}
        </div>
      </div>
      <MainNav place="bottom" active="saved" />
    </div>
  )
}

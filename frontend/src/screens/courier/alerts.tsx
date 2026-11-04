import { useMutation, useQueryClient } from '@tanstack/react-query'
import { BellOff, CircleAlert, EllipsisVertical, Info, Luggage, Package, Pause, Pencil, Play, Plus, Route, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { fa, formatDate } from '@/lib/format'
import { track } from '@/lib/report'
import { ALERTS_KEY, useAlerts } from './alert-sheet'
import { courierFetch, errorCode, patchJson, unwrap } from './api'
import { monthLabel, packageSize, placeKey, routeProps, SIZES, upcomingMonths, type Side } from './format'
import { H1, ME_KEY, Page, Stack } from './page'
import { Section, SingleChips, SizeTiles } from './parts'
import { RouteBar, RouteSheet } from './route-sheet'
import { failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type AlertRow = components['schemas']['Alert']
type AlertSaved = components['schemas']['AlertSaved']

const WEIGHTS: Record<Side, Record<string, string>> = {
  DEMAND: { '0.50': 'doc', '1.00': '1', '3.00': '3', '5.00': '3p' },
  SUPPLY: { '0.50': 'doc', '1.00': '1', '3.00': '3', '10.00': '3p' },
}

function subLabel(row: AlertRow) {
  const parts = [row.date_to ? `تا ${formatDate(row.date_to)}` : '', row.weight_kg ? packageSize(row.weight_kg) : ''].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'بدون تاریخ'
}

// C11.3 [NEW]. The alert keeps a date window and a weight; the sheet reads them back as a month and a size.
function EditSheet({ row, onClose }: { row: AlertRow; onClose: () => void }) {
  const client = useQueryClient()
  const side = row.side as Side
  const months = upcomingMonths()
  const [route, setRoute] = useState({ from: placeKey(row.origin), to: placeKey(row.destination) })
  const [month, setMonth] = useState(() => (row.date_to && months.includes(row.date_to.slice(0, 7)) ? row.date_to.slice(0, 7) : ''))
  const [size, setSize] = useState(() => (row.weight_kg ? (WEIGHTS[side][row.weight_kg] ?? '') : ''))
  const [picking, setPicking] = useState<'from' | 'to' | null>(null)
  const [conflict, setConflict] = useState(false)
  const save = useMutation({
    mutationFn: () => unwrap(patchJson<AlertSaved>(`me/alerts/${row.id}/`, { origin: route.from, destination: route.to, month, size })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ALERTS_KEY })
      track('courier.c11.alert_edited')
      track('courier_alert_edit', { id: row.id, side, month: month || null, size: size || null, ...routeProps(route.from, route.to) })
      onClose()
    },
    onError: (error) => {
      const code = errorCode(error)
      track('courier_alert_edit_failed', { id: row.id, code })
      if (code === 'alert_exists') {
        setConflict(true)
        track('courier.c11.alert_conflict')
      } else if (code === 'alert_not_found') {
        void client.invalidateQueries({ queryKey: ALERTS_KEY })
        showToast({ message: 'این گوش به زنگ حذف شده است.', icon: 'info' })
        onClose()
      } else {
        showToast({ message: 'ذخیره نشد', icon: 'alert', overSheet: true, action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'alert_edit' }), save.mutate()) } })
      }
    },
  })
  return (
    <>
      <AppSheet
        open={picking === null}
        onClose={onClose}
        label="ویرایش گوش به زنگ"
        title="ویرایش گوش به زنگ"
        testId="alert-edit"
        foot={
          <Button
            className="w-full"
            disabled={!route.from || !route.to || save.isPending}
            onClick={() => {
              track('courier_alert_edit_save_tap', { id: row.id })
              save.mutate()
            }}
            data-testid="alert-edit-save"
          >
            ذخیره
          </Button>
        }
      >
        {conflict ? (
          <Alert variant="destructive" data-testid="alert-conflict">
            <CircleAlert />
            <AlertDescription>این مسیر را قبلاً گوش به زنگ کرده‌اید.</AlertDescription>
          </Alert>
        ) : null}
        <RouteBar value={route} onOpen={setPicking} empty="انتخاب کنید" />
        <Section title="زمان">
          <SingleChips label="زمان" value={month} options={months.map((key, index) => ({ value: key, label: monthLabel(key, index === 0) }))} onChange={(next) => (track('courier_alert_edit_field', { id: row.id, field: 'month' }), setMonth(next))} testId="alert-edit-months" />
        </Section>
        <Section title="اندازه">
          <SizeTiles value={size} options={SIZES[side]} trip={side === 'SUPPLY'} onChange={(next) => (track('courier_alert_edit_field', { id: row.id, field: 'size' }), setSize(next))} testId="alert-edit-sizes" />
        </Section>
      </AppSheet>
      <RouteSheet
        open={picking !== null}
        side={picking ?? 'from'}
        value={route}
        allowAny={false}
        frequent={false}
        onClose={() => setPicking(null)}
        onDone={(next) => {
          track('courier_alert_edit_field', { id: row.id, field: 'route' })
          setRoute(next)
          setConflict(false)
          setPicking(null)
        }}
      />
    </>
  )
}

// C11: pause with undo, delete with a confirm, and the count against the cap of five.
export function AlertsScreen() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const alerts = useAlerts()
  const [editing, setEditing] = useState<AlertRow | null>(null)
  const [deleting, setDeleting] = useState<AlertRow | null>(null)
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ALERTS_KEY })
    void client.invalidateQueries({ queryKey: ME_KEY })
  }
  const gone = () => {
    refresh()
    showToast({ message: 'این گوش به زنگ حذف شده است.', icon: 'info' })
    track('courier.c11.alert_gone')
    track('courier_alert_gone')
  }

  const toggle = useMutation({
    mutationFn: ({ row, active }: { row: AlertRow; active: boolean }) => unwrap(patchJson<AlertSaved>(`me/alerts/${row.id}/`, { active })),
    onSuccess: (_saved, { row, active }) => {
      refresh()
      if (!active) showToast({ message: 'خاموش شد', icon: 'info', action: { label: 'برگرداندن', onClick: () => (track('courier_alert_pause_undo', { id: row.id }), toggle.mutate({ row, active: true })) } })
      else showToast({ message: 'روشن شد', icon: 'check' })
      track(active ? 'courier.c11.alert_on' : 'courier.c11.alert_paused')
      track(active ? 'courier_alert_resume' : 'courier_alert_pause', { id: row.id })
    },
    onError: (error, variables) => {
      if (errorCode(error) === 'alert_not_found') return gone()
      track('courier.c11.alert_rollback')
      track('courier_alert_toggle_failed', { id: variables.row.id, active: variables.active, code: errorCode(error) })
      showToast({ message: variables.active ? 'روشن نشد' : 'خاموش نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'alert_toggle' }), toggle.mutate(variables)) } })
    },
  })
  const remove = useMutation({
    mutationFn: (row: AlertRow) => unwrap(courierFetch<null>(`me/alerts/${row.id}/`, { method: 'DELETE' })),
    onSuccess: (_saved, row) => {
      refresh()
      showToast({ message: 'حذف شد', icon: 'check' })
      track('courier.c11.alert_deleted')
      track('courier_alert_delete', { id: row.id })
    },
    onError: (error, row) => {
      if (errorCode(error) === 'alert_not_found') return gone()
      track('courier_alert_delete_failed', { id: row.id, code: errorCode(error) })
      showToast({ message: 'حذف نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'alert_delete' }), remove.mutate(row)) } })
    },
  })

  const rows = alerts.data?.alerts ?? []
  const cap = alerts.data?.cap ?? 5
  useEffect(() => {
    if (alerts.isSuccess) track(rows.length ? (rows.length >= cap ? 'courier.c11.alerts_full' : 'courier.c11.alerts_view') : 'courier.c11.alerts_empty')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts.isSuccess])

  useEffect(() => {
    if (!alerts.isSuccess) return
    track('courier_alerts_view', { count: rows.length, cap, sending_on: Boolean(alerts.data?.sending_on) })
    if (!rows.length) track('courier_alerts_empty')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts.isSuccess])

  if (alerts.isLoading) {
    return (
      <Page tab="alerts" testId="alerts-loading">
        <Stack center gap="gap-3">
          <Skeleton className="h-8 w-[60%]" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </Stack>
      </Page>
    )
  }
  if (alerts.isError) return <Page tab="alerts">{failedScreen(alerts.error, () => void alerts.refetch())}</Page>
  if (!rows.length) {
    return (
      <Page tab="alerts" testId="alerts-empty">
        <StateScreen icon={BellOff} title="هنوز گوش به زنگی ندارید">
          <Button
            onClick={() => {
              track('courier_alerts_search_tap', { surface: 'empty' })
              navigate('/')
            }}
            data-testid="alerts-search"
          >
            <Route />
            جستجوی مسیر
          </Button>
        </StateScreen>
      </Page>
    )
  }
  return (
    <Page tab="alerts" testId="alerts">
      <Stack center gap="gap-3">
        <div className="flex items-center justify-between gap-2">
          <H1>گوش به زنگ مسیر</H1>
          <Badge variant="secondary" data-testid="alerts-count">
            {fa(rows.length)} از {fa(cap)}
          </Badge>
        </div>
        {!alerts.data?.sending_on ? (
          <Alert data-testid="alerts-sending-off">
            <Info />
            <AlertDescription>فرستادن خبرها به‌زودی روشن می‌شود؛ گوش به زنگ‌های شما ذخیره می‌ماند.</AlertDescription>
          </Alert>
        ) : null}
        {rows.map((row) => {
          const Icon = row.side === 'SUPPLY' ? Luggage : Package
          const busy = toggle.isPending && toggle.variables?.row.id === row.id
          return (
            <Card key={row.id} className="!gap-3 !p-4" data-testid="alert-row" data-id={row.id}>
              <div className="flex items-start gap-2">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="flex items-center gap-1.5 font-bold">
                    <Icon className="size-4 shrink-0 text-primary" aria-hidden />
                    <bdi className="truncate">{`${row.origin.name_fa} ← ${row.destination.name_fa}`}</bdi>
                  </span>
                  <span className="text-[13px] text-muted-foreground">{subLabel(row)}</span>
                  {!row.active ? (
                    <div className="mt-1">
                      <Badge variant="outline" data-testid="alert-off">
                        <Pause />
                        خاموش
                      </Badge>
                    </div>
                  ) : null}
                </div>
                <DropdownMenu
                  dir="rtl"
                  onOpenChange={(open) => {
                    if (open) track('courier_alert_menu_open', { id: row.id })
                  }}
                >
                  {/* Not asChild: Button takes no ref on React 18, and the menu positions itself from the trigger's. */}
                  <DropdownMenuTrigger className={buttonVariants({ variant: 'ghost', size: 'icon' })} aria-label="گزینه‌ها" disabled={busy} data-testid="alert-menu">
                    <EllipsisVertical />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onSelect={() => {
                        track('courier_alert_edit_open', { id: row.id })
                        setEditing(row)
                      }}
                      data-testid="alert-edit-open"
                    >
                      <Pencil />
                      ویرایش
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => toggle.mutate({ row, active: !row.active })} data-testid="alert-toggle">
                      {row.active ? <Pause /> : <Play />}
                      {row.active ? 'خاموش' : 'روشن'}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      variant="destructive"
                      onSelect={() => {
                        track('courier_alert_delete_open', { id: row.id })
                        setDeleting(row)
                      }}
                      data-testid="alert-delete-open"
                    >
                      <Trash2 />
                      حذف
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </Card>
          )
        })}
        {rows.length < cap ? (
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              track('courier_alerts_search_tap', { surface: 'new' })
              navigate('/')
            }}
            data-testid="alerts-new"
          >
            <Plus />
            گوش به زنگ جدید
          </Button>
        ) : null}
      </Stack>
      {editing ? <EditSheet row={editing} onClose={() => setEditing(null)} /> : null}
      <AlertDialog open={deleting !== null} onOpenChange={(open) => !open && setDeleting(null)}>
        <AlertDialogContent dir="rtl" data-testid="alert-delete">
          <AlertDialogHeader>
            <AlertDialogTitle>این گوش به زنگ حذف شود؟</AlertDialogTitle>
            {deleting ? <AlertDialogDescription>{`${deleting.origin.name_fa} ← ${deleting.destination.name_fa}`}</AlertDialogDescription> : null}
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => track('courier_alert_delete_cancel', { id: deleting?.id })}>انصراف</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                track('courier_alert_delete_confirm', { id: deleting?.id })
                if (deleting) remove.mutate(deleting)
              }}
              data-testid="alert-delete-confirm"
            >
              حذف
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Page>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BellRing, CalendarDays, CircleAlert, Package, Route, Scale } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { track } from '@/lib/report'
import { courierFetch, CourierError, errorCode, postJson, unwrap } from './api'
import { monthLabel, placeKey, sizeName, upcomingMonths, type Side } from './format'
import { ME_KEY } from './page'
import { Fact, FactGrid } from './parts'
import { placeLabel, RouteBar, RouteSheet, useLocations, type RouteValue } from './route-sheet'
import type { components } from '@/api/courier-schema.gen'

type Alerts = components['schemas']['Alerts']
type AlertSaved = components['schemas']['AlertSaved']

export const ALERTS_KEY = ['courier', 'alerts']

export function useAlerts(enabled = true) {
  return useQuery({ queryKey: ALERTS_KEY, queryFn: () => unwrap(courierFetch<Alerts>('me/alerts/')), enabled })
}

export type AlertDraft = RouteValue & { side: Side; month: string; size: string }

// C10.1 to C10.7. Opens only from the count bar and the empty list. Alerts arrive in the bot only.
export function AlertSheet({ open, draft, onClose, onRoute }: { open: boolean; draft: AlertDraft; onClose: () => void; onRoute?: (route: RouteValue) => void }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const locations = useLocations()
  const [cap, setCap] = useState(false)
  const [editing, setEditing] = useState<'from' | 'to' | null>(null)
  const [route, setRoute] = useState<RouteValue>({ from: draft.from, to: draft.to })
  const needsRoute = !route.from || !route.to
  useEffect(() => {
    if (!open) return
    setCap(false)
    setRoute({ from: draft.from, to: draft.to })
    track('courier.c10.open')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const months = upcomingMonths()
  const save = useMutation({
    mutationFn: () => unwrap(postJson<AlertSaved>('me/alerts/', { side: draft.side, origin: route.from, destination: route.to, month: draft.month, size: draft.size })),
    onSuccess: (saved, _vars) => {
      void client.invalidateQueries({ queryKey: ALERTS_KEY })
      void client.invalidateQueries({ queryKey: ME_KEY })
      onRoute?.(route)
      onClose()
      const manage = { label: 'مدیریت', onClick: () => navigate('/alerts') }
      if (!saved.sending_on) showToast({ message: 'ذخیره شد. فرستادن خبرها به‌زودی روشن می‌شود.', icon: 'info' })
      else if (saved.created) showToast({ message: 'گوش به زنگ روشن شد', icon: 'check', action: manage })
      else showToast({ message: 'گوش به زنگ دوباره روشن شد', icon: 'check' })
      track('courier.c10.saved', { created: saved.created, sending_on: saved.sending_on })
    },
    onError: (error) => {
      const code = errorCode(error)
      if (code === 'alert_cap') {
        setCap(true)
        track('courier.c10.cap')
        return
      }
      if (error instanceof CourierError && (code === 'route_required' || code === 'same_city')) {
        setRoute({ from: '', to: '' })
        return
      }
      onClose()
      showToast({ message: 'گوش به زنگ ذخیره نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => save.mutate() } })
      track('courier.c10.save_failed')
    },
  })
  return (
    <>
      <AppSheet
        open={open && editing === null}
        onClose={onClose}
        label="گوش به زنگ"
        title="گوش به زنگ"
        testId="alert-sheet"
        foot={
          cap ? (
            <Button className="w-full" onClick={() => navigate('/alerts')} data-testid="alert-manage">
              مدیریت گوش به زنگ‌ها
            </Button>
          ) : (
            <Button className="w-full" disabled={needsRoute || save.isPending} onClick={() => save.mutate()} data-testid="alert-save">
              <Bell />
              روشن کردن
            </Button>
          )
        }
      >
        {cap ? (
          <Alert variant="destructive" data-testid="alert-cap">
            <CircleAlert />
            <AlertDescription>حداکثر ۵ گوش به زنگ دارید. یکی را حذف کنید.</AlertDescription>
          </Alert>
        ) : null}
        {needsRoute ? (
          <div data-testid="alert-need-route">
            <RouteBar value={route} onOpen={setEditing} empty="انتخاب کنید" />
          </div>
        ) : (
          <Card className="!gap-3 !p-4">
            <FactGrid>
              <Fact icon={Route} label="مسیر" value={<bdi>{`${placeLabel(route.from, locations.data)} ← ${placeLabel(route.to, locations.data)}`}</bdi>} />
              <Fact icon={Package} label="طرف بازار" value={draft.side === 'DEMAND' ? 'بسته دارم' : 'مسافرم'} />
              {draft.month ? <Fact icon={CalendarDays} label="زمان" value={monthLabel(draft.month, draft.month === months[0])} /> : null}
              {draft.size ? <Fact icon={Scale} label="اندازه" value={sizeName(draft.side, draft.size)} /> : null}
            </FactGrid>
          </Card>
        )}
      </AppSheet>
      <RouteSheet
        open={editing !== null}
        side={editing ?? 'from'}
        value={route}
        allowAny={false}
        frequent={false}
        onClose={() => setEditing(null)}
        onDone={(next) => {
          setRoute(next)
          setEditing(null)
        }}
      />
    </>
  )
}

// The alert entry on every courier list: it reads on when this exact route and side already has a live alert.
export function AlertButton({ draft, onOpen }: { draft: AlertDraft; onOpen: () => void }) {
  const alerts = useAlerts()
  const on = alerts.data?.alerts.some((row) => row.active && row.side === draft.side && placeKey(row.origin) === draft.from && placeKey(row.destination) === draft.to)
  return on ? (
    <Button variant="secondary" size="sm" onClick={onOpen} data-testid="alert-button-on">
      <BellRing />
      گوش به زنگ روشن
    </Button>
  ) : (
    <Button variant="outline" size="sm" onClick={onOpen} data-testid="alert-button">
      <Bell />
      گوش به زنگ
    </Button>
  )
}

import { useQuery } from '@tanstack/react-query'
import { Bell, BellRing, Bot } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { AlertRow, AlertSave } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { rateLimitedToast, showToast } from '@/components/app-toast'
import { Segmented } from '@/components/segmented'
import { StateBox } from '@/components/state-screen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { ALERTS_KEY, alertsFetch, fetchAlerts } from '@/lib/alerts'
import { alertName, filterChips, filtersOfView, sameSearch } from '@/lib/alert-view'
import type { SavedCity } from '@/lib/city'
import { useHasSession } from '@/lib/features'
import type { View } from '@/lib/listing-view'
import { t } from '@/lib/locale'
import { openExternal } from '@/lib/links'
import { queryClient } from '@/lib/query'
import { track } from '@/lib/report'
import { CapSheet, DuplicateSheet, NOW_LABEL, QUIET } from './alerts'

type Sheet = { name: 'create' } | { name: 'cap' } | { name: 'duplicate'; row: AlertRow } | { name: 'bot'; url: string } | null

// A search saved on this visit, before the list of alerts has come back. The chip reads «روشن» at once.
const savedHere = new Set<string>()
const hereKey = (slug: string, view: View) => `${slug}|${JSON.stringify(filtersOfView(view))}`

// The tab holding this person's alerts, whatever the saved tab would open on by itself.
const SAVED_ALERTS = '/saved?tab=alerts'

// F15: the bell in the count bar and all its sheets. Only rendered with alerts on and messages going out (F15#7).
export default function AlertEntry({ view, city, count }: { view: View; city: SavedCity; count: number }) {
  const navigate = useNavigate()
  const session = useHasSession()
  const list = useQuery({ queryKey: ALERTS_KEY, queryFn: fetchAlerts, enabled: session })
  const [sheet, setSheet] = useState<Sheet>(null)
  const [delivery, setDelivery] = useState<AlertRow['delivery']>('now')
  const [sending, setSending] = useState(false)
  const filters = useMemo(() => filtersOfView(view), [view])
  const name = alertName(view, city.country, city.name_fa)
  const chips = filterChips(view, city.country)
  const budget = view.min != null || view.max != null
  const on = savedHere.has(hereKey(city.slug, view)) || Boolean(list.data?.results.some((row) => row.city_slug === city.slug && row.alert_state === 'active' && sameSearch(row.filters, filters)))
  const see = () => {
    track('alert_see')
    setSheet(null)
    navigate(SAVED_ALERTS)
  }

  useEffect(() => {
    if (sheet?.name === 'bot') track('bot_closed')
    if (sheet?.name === 'cap') track('alert_capped')
    if (sheet?.name === 'duplicate') track('alert_duplicate')
  }, [sheet?.name])

  const save = async () => {
    if (sending) return // A double tap is one tap. A retry finds the same row on the server too.
    track('alert_submit', { delivery })
    setSending(true)
    const result = await alertsFetch<AlertSave>('/api/v1/me/alerts/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ country: city.country, city: city.city, filters, delivery }) })
    setSending(false)
    if (!result.ok) {
      track('alert_failed', { code: result.code })
      if (result.code === 'alert_cap') return setSheet({ name: 'cap' })
      if (QUIET.has(result.code)) return setSheet(null)
      if (result.code === 'rate_limited') return rateLimitedToast(() => void save(), 'alert')
      return void showToast({ id: 'alert', message: t('ذخیره نشد'), icon: 'alert', overSheet: true, action: { label: t('تلاش مجدد'), onClick: () => void save() } })
    }
    const { save_outcome: outcome, alert, bot_open: botOpen, bot_url: botUrl } = result.data
    void queryClient.invalidateQueries({ queryKey: ALERTS_KEY })
    if (outcome === 'duplicate') return setSheet({ name: 'duplicate', row: alert })
    savedHere.add(hereKey(city.slug, view))
    track(outcome === 'reactivated' ? 'alert_reactivated' : count === 0 ? 'alert_saved_empty' : 'alert_saved')
    if (!botOpen) return setSheet({ name: 'bot', url: botUrl }) // F15#3: saved either way.
    setSheet(null)
    const said = outcome === 'reactivated' ? 'گوش به زنگ دوباره روشن شد' : 'گوش به زنگ ذخیره شد'
    showToast({ id: 'alert', message: t(said), icon: 'check', action: { label: t('دیدن'), onClick: () => navigate(SAVED_ALERTS) } })
  }

  return (
    <>
      {on ? (
        <Button variant="secondary" size="icon" aria-label={t('گوش به زنگ روشن')} data-testid="alert-chip" onClick={() => { track('alert_chip_on'); navigate(SAVED_ALERTS) }}>
          <BellRing />
        </Button>
      ) : (
        <Button variant="ghost" size="icon" aria-label={t('گوش به زنگ')} data-testid="alert-chip" onClick={() => { track('alert_start', { from: 'list', count }); setSheet({ name: 'create' }) }}>
          <Bell />
        </Button>
      )}
      {sheet?.name === 'create' ? (
        <AppSheet
          open
          onClose={() => { track('alert_dismissed'); setSheet(null) }}
          label={t('گوش به زنگ')}
          title={t('گوش به زنگ')}
          testId="alert-sheet"
          foot={
            <Button className="w-full" disabled={sending} onClick={() => void save()}>
              {sending ? <Spinner /> : null}
              {t('ذخیره')}
            </Button>
          }
        >
          <p className="m-0 text-sm leading-[22px]" data-testid="alert-explain">{t('وقتی آگهی مطابقی پیدا شد، به شما پیام می‌دهیم.')}</p>
          <Field>
            <FieldLabel htmlFor="alert-name">{t('نام')}</FieldLabel>
            <Input id="alert-name" dir="auto" readOnly value={name.title} />
          </Field>
          <div className="flex flex-wrap gap-2" data-testid="alert-tags">
            <Badge variant="secondary">{city.name_fa}</Badge>
            {chips.map((chip) => (
              <Badge key={chip.id} variant="secondary">
                {chip.label}
              </Badge>
            ))}
            {budget && view.unpriced ? <Badge variant="secondary">{t('بدون قیمت هم بیاید')}</Badge> : null}
          </div>
          <Segmented
            label={t('ارسال')}
            value={delivery}
            onChange={(next) => { track('alert_delivery', { delivery: next, from: 'create' }); setDelivery(next) }}
            options={[
              { value: 'now', label: t(NOW_LABEL) },
              { value: 'daily', label: t('روزانه، ساعت ۹') },
            ]}
          />
        </AppSheet>
      ) : null}
      {sheet?.name === 'bot' ? (
        <AppSheet open onClose={null} onBack={() => setSheet(null)} label={t('باز کردن ربات')} testId="alert-bot">
          <StateBox icon={Bot} title={t('برای دریافت پیام، ربات را باز کنید')} />
          <div className="flex flex-col gap-2">
            <Button
              className="w-full"
              onClick={() => {
                track('bot_open', { from: 'alert' })
                openExternal(sheet.url)
                setSheet(null)
              }}
            >
              {t('باز کردن ربات')}
            </Button>
            <Button variant="outline" className="w-full" onClick={() => { track('bot_later'); setSheet(null) }}>
              {t('بعداً')}
            </Button>
          </div>
        </AppSheet>
      ) : null}
      {sheet?.name === 'cap' ? <CapSheet cap={list.data?.cap ?? 5} onManage={see} onClose={() => setSheet(null)} /> : null}
      {sheet?.name === 'duplicate' ? <DuplicateSheet row={sheet.row} onSee={see} onClose={() => setSheet(null)} /> : null}
    </>
  )
}

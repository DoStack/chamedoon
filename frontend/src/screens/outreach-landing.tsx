import { BellRing, CircleCheck, Send } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { OutreachAlert, OutreachLanding } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { needsInternetToast, rateLimitedToast, saveFailedToast } from '@/components/app-toast'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { currency, fa } from '@/lib/format'
import { openExternal } from '@/lib/links'
import { confirmOutreachAlert, loadOutreachLanding } from '@/lib/outreach'
import { track } from '@/lib/report'

type Step = 'ask' | 'sending' | 'done'

// The ready alert from an outreach message: what it watches, and one tap to turn it on.
export function AlertReadySheet({ landing, onClose }: { landing: OutreachLanding & { alert: OutreachAlert }; onClose: () => void }) {
  const [step, setStep] = useState<Step>('ask')
  const alert = landing.alert
  useEffect(() => track('outreach_alert_shown', { alert_id: alert.id, match_count: alert.match_count }), [alert.id, alert.match_count])

  const confirm = async () => {
    if (navigator.onLine === false) return needsInternetToast()
    track('outreach_alert_confirm_tap', { alert_id: alert.id })
    setStep('sending')
    const result = await confirmOutreachAlert(alert.id)
    if (result.ok) {
      track('outreach_alert_confirmed', { alert_id: alert.id })
      return setStep('done')
    }
    setStep('ask')
    track('outreach_alert_confirm_failed', { code: result.code })
    if (result.code === 'rate_limited') rateLimitedToast(() => void confirm())
    else saveFailedToast(() => void confirm())
  }

  const done = step === 'done'
  return (
    <AppSheet
      open
      onClose={onClose}
      label="گوش‌به‌زنگ"
      testId="outreach-alert"
      title={done ? 'گوش‌به‌زنگ روشن شد' : 'گوش‌به‌زنگ آماده است'}
      foot={
        done ? (
          landing.bot_url ? (
            <Button
              variant="outline"
              className="w-full"
              onClick={() => {
                track('outreach_bot_tap')
                openExternal(landing.bot_url)
              }}
              data-testid="outreach-bot"
            >
              <Send />
              برای پیام فوری، ربات رو start کنید
            </Button>
          ) : null
        ) : (
          <Button className="w-full" disabled={step === 'sending'} onClick={() => void confirm()} data-testid="outreach-confirm">
            {step === 'sending' ? <Spinner /> : <BellRing />}
            روشنش کن
          </Button>
        )
      }
    >
      {done ? (
        <p className="m-0 flex items-center gap-2 text-sm" data-testid="outreach-done">
          <CircleCheck className="size-[18px] flex-none" aria-hidden />
          هر آگهی تازه‌ای که بخوره، خبرتون می‌کنیم.
        </p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm" data-testid="outreach-alert-facts">
          <li>
            شهر: <b>{alert.city || landing.city}</b>
          </li>
          {alert.max_price != null ? (
            <li>
              بودجه تا <b>{fa(alert.max_price)}</b> <bdi dir="ltr">{currency(alert.currency)}</bdi>
            </li>
          ) : null}
          <li data-testid="outreach-match-count">
            {alert.match_count > 0 ? `همین حالا ${fa(alert.match_count)} آگهی می‌خوره.` : 'فعلاً آگهی‌ای نمی‌خوره، تازه‌ها رو خبر می‌دیم.'}
          </li>
        </ul>
      )}
    </AppSheet>
  )
}

// Mounted once the shell is ready. Does nothing unless the launch came from an outreach message to this person.
export function OutreachLandingLayer() {
  const [landing, setLanding] = useState<OutreachLanding | null>(null)
  const [closed, setClosed] = useState(false)
  useEffect(() => {
    let live = true
    void loadOutreachLanding().then((found) => {
      if (live) setLanding(found)
    })
    return () => {
      live = false
    }
  }, [])
  if (closed || !landing?.alert || landing.alert.active) return null
  return <AlertReadySheet landing={landing as OutreachLanding & { alert: OutreachAlert }} onClose={() => setClosed(true)} />
}

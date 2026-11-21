// The login sheet of web mode (browser, outside Telegram). Loaded only in a browser, never in the Mini App.
//
// «ورود با تلگرام»: the server gives a one-time link to the housing bot and a two-digit number; the person opens the bot
// (button, or the QR code from a computer), taps the same number there, and this sheet signs in by itself.
// «ورود با ایمیل» (only when the server turns it on): a 6-digit code by email.
import qrcode from 'qrcode-generator'
import { Mail, RefreshCw, Send } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { track } from '@/lib/report'
import { finishLogin, setWebUser, useWeb, type WebUser } from '@/lib/web'

type Start = { code: string; match: number; poll_token: string; bot_url: string; expires_in: number }
type Step = 'choose' | 'telegram' | 'email' | 'code'
type Phase = 'starting' | 'waiting' | 'opened' | 'expired' | 'denied' | 'failed' | 'limited'

const POLL_MS = 2000
const JSON_HEADERS = { 'Content-Type': 'application/json', Accept: 'application/json' }

async function post<T>(url: string, body: unknown): Promise<{ ok: boolean; status: number; data: T | null }> {
  try {
    const response = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: JSON_HEADERS, body: JSON.stringify(body) })
    return { ok: response.ok, status: response.status, data: (await response.json().catch(() => null)) as T | null }
  } catch {
    return { ok: false, status: 0, data: null }
  }
}

function QrCode({ url }: { url: string }) {
  const path = useMemo(() => {
    const qr = qrcode(0, 'M')
    qr.addData(url)
    qr.make()
    const n = qr.getModuleCount()
    let d = ''
    for (let row = 0; row < n; row++) for (let col = 0; col < n; col++) if (qr.isDark(row, col)) d += `M${col + 4} ${row + 4}h1v1h-1z`
    return { d, size: n + 8 }
  }, [url])
  return (
    <svg viewBox={`0 0 ${path.size} ${path.size}`} className="size-44 rounded-xl bg-white" role="img" aria-label="QR" data-testid="web-login-qr" shapeRendering="crispEdges">
      <path d={path.d} fill="#000" />
    </svg>
  )
}

function useIsDesktop() {
  return useMemo(() => !window.matchMedia('(pointer: coarse), (max-width: 767px)').matches, [])
}

function TelegramStep({ onDone }: { onDone: (user: WebUser, prefs: Record<string, unknown>) => void }) {
  const [start, setStart] = useState<Start | null>(null)
  const [phase, setPhase] = useState<Phase>('starting')
  const [round, setRound] = useState(0)
  const desktop = useIsDesktop()
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    let alive = true
    setPhase('starting')
    setStart(null)
    void post<Start>('/api/v1/auth/web/start/', {}).then((result) => {
      if (!alive) return
      if (result.ok && result.data?.bot_url) {
        setStart(result.data)
        setPhase('waiting')
        track('web_login_start')
      } else setPhase(result.status === 429 ? 'limited' : 'failed')
    })
    return () => {
      alive = false
    }
  }, [round])

  useEffect(() => {
    if (!start) return
    let alive = true
    let timer = 0
    const tick = async () => {
      try {
        const response = await fetch('/api/v1/auth/web/poll/', { credentials: 'same-origin', headers: { 'X-Poll-Token': start.poll_token, Accept: 'application/json' } })
        const body = (await response.json().catch(() => null)) as { status?: string; opened?: boolean; user?: WebUser; prefs?: Record<string, unknown> } | null
        if (!alive) return
        if (body?.status === 'ok' && body.user) {
          track('web_login_done', { via: 'telegram' })
          done.current(body.user, body.prefs ?? {})
          return
        }
        if (body?.status === 'denied') return setPhase('denied')
        if (body?.status === 'expired' || body?.status === 'invalid') return setPhase('expired')
        if (body?.opened) setPhase('opened')
      } catch {
        // A dropped poll is retried on the next tick.
      }
      if (alive) timer = window.setTimeout(tick, POLL_MS)
    }
    timer = window.setTimeout(tick, POLL_MS)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
  }, [start])

  if (phase === 'starting') return <div className="flex justify-center py-8"><Spinner /></div>
  if (phase !== 'waiting' && phase !== 'opened') {
    const text = { expired: 'زمان این ورود تمام شد.', denied: 'این ورود در تلگرام لغو شد.', failed: 'ورود الان ممکن نشد.', limited: 'تلاش‌ها زیاد بود. چند دقیقه بعد دوباره امتحان کنید.' }[phase]
    return (
      <div className="flex flex-col items-center gap-3 py-4 text-center" data-testid="web-login-retry">
        <p className="text-sm font-semibold">{text}</p>
        <Button onClick={() => setRound((n) => n + 1)}>
          <RefreshCw />
          دوباره
        </Button>
      </div>
    )
  }
  return (
    <div className="flex flex-col items-center gap-4 text-center" data-testid="web-login-telegram">
      <div className="flex flex-col items-center gap-1">
        <span className="text-sm text-muted-foreground">عدد شما</span>
        <span className="font-mono text-5xl font-black tracking-widest tabular-nums" data-testid="web-login-match" dir="ltr">
          {start!.match}
        </span>
      </div>
      <Button asChild className="w-full">
        <a href={start!.bot_url} target="_blank" rel="noopener noreferrer" onClick={() => track('web_login_open_bot')}>
          <Send />
          باز کردن ربات چمدون در تلگرام
        </a>
      </Button>
      {desktop ? <QrCode url={start!.bot_url} /> : null}
      <p className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="web-login-status">
        <Spinner className="size-4" />
        {phase === 'opened' ? 'در تلگرام همین عدد را بزنید' : 'در انتظار تأیید در تلگرام'}
      </p>
    </div>
  )
}

function EmailStep({ onDone }: { onDone: (user: WebUser, prefs: Record<string, unknown>) => void }) {
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [sent, setSent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const send = async () => {
    setBusy(true)
    setError('')
    const result = await post<{ sent: boolean }>('/api/v1/auth/email/start/', { email: email.trim() })
    setBusy(false)
    if (result.ok) return setSent(true)
    setError(result.status === 429 ? 'تلاش‌ها زیاد بود. کمی بعد دوباره امتحان کنید.' : result.status === 400 ? 'ایمیل درست نیست.' : 'ارسال ممکن نشد.')
  }
  const verify = async () => {
    setBusy(true)
    setError('')
    const result = await post<{ user: WebUser; prefs: Record<string, unknown> }>('/api/v1/auth/email/verify/', { email: email.trim(), code: code.trim() })
    setBusy(false)
    if (result.ok && result.data?.user) {
      track('web_login_done', { via: 'email' })
      return onDone(result.data.user, result.data.prefs ?? {})
    }
    setError('کد درست نیست یا منقضی شده است.')
  }

  return (
    <div className="flex flex-col gap-3" data-testid="web-login-email">
      <Input type="email" dir="ltr" autoComplete="email" placeholder="you@example.com" value={email} disabled={sent} onChange={(event) => setEmail(event.target.value)} />
      {sent ? <Input inputMode="numeric" dir="ltr" autoComplete="one-time-code" maxLength={6} placeholder="------" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))} /> : null}
      {error ? <p className="text-sm font-semibold text-destructive">{error}</p> : null}
      {sent ? (
        <Button disabled={busy || code.length !== 6} onClick={() => void verify()}>
          {busy ? <Spinner /> : null}
          ورود
        </Button>
      ) : (
        <Button disabled={busy || !email.includes('@')} onClick={() => void send()}>
          {busy ? <Spinner /> : <Mail />}
          ارسال کد
        </Button>
      )}
    </div>
  )
}

export default function WebLoginHost() {
  const { ask, session } = useWeb()
  const [step, setStep] = useState<Step>('choose')
  const emailOn = Boolean(session?.email_login) && !ask?.telegram

  useEffect(() => {
    if (ask) {
      setStep(emailOn ? 'choose' : 'telegram')
      track('web_login_sheet', { reason: ask.reason })
    }
  }, [ask, emailOn])

  const done = (user: WebUser, prefs: Record<string, unknown>) => {
    setWebUser(user, prefs)
    finishLogin(true)
  }
  const close = () => finishLogin(false)

  return (
    <AppSheet open={Boolean(ask)} onClose={close} label="ورود" title={ask?.telegram ? 'وصل کردن تلگرام' : 'ورود به چمدون'} testId="web-login-sheet">
      {step === 'choose' ? (
        <div className="flex flex-col gap-2">
          <Button onClick={() => setStep('telegram')} data-testid="web-login-telegram-button">
            <Send />
            ورود با تلگرام
          </Button>
          <Button variant="outline" onClick={() => setStep('email')}>
            <Mail />
            ورود با ایمیل
          </Button>
        </div>
      ) : step === 'telegram' ? (
        <TelegramStep onDone={done} />
      ) : (
        <EmailStep onDone={done} />
      )}
    </AppSheet>
  )
}

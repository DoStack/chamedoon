import { Clock, ExternalLink, KeyRound, LifeBuoy, Lock, RefreshCw, Route, SearchX, Send, ServerCrash, WifiOff, Wrench } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { track } from '@/lib/report'
import { getStartParam, getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { boot, legacyUrl, type Update } from './access'
import { errorCode, type CourierCode } from './api'

const BOT = (import.meta.env.VITE_COURIER_BOT as string | undefined) ?? 'chamedoon_bot'
const MAIN_CHANNEL = (import.meta.env.VITE_COURIER_CHANNEL as string | undefined) ?? 'chamedoon_channel'

export const reopenUrl = () => {
  const start = getStartParam()
  return `https://t.me/${BOT}?startapp=${start ? encodeURIComponent(start) : 'courier'}`
}

export const requestLink = (id: number) => `https://t.me/${BOT}?startapp=explore_${id}`

async function copy(text: string) {
  return (
    (await navigator.clipboard?.writeText(text).then(
      () => true,
      () => false,
    )) ?? false
  )
}

// t.me links stay in Telegram; openLink is for everything else. C18.13 when nothing opens: copy instead.
export function openLink(url: string) {
  if (!url.startsWith('https://')) return false
  const app = getWebApp()
  const inTelegram = url.startsWith('https://t.me/')
  const open = inTelegram ? app?.openTelegramLink : app?.openLink
  if (open && (!inTelegram || isVersionAtLeast('6.1'))) {
    try {
      open.call(app, url)
      return true
    } catch {
      // Falls through to the copy below.
    }
  } else if (!app?.initData) {
    window.open(url, '_blank', 'noopener')
    return true
  }
  track('courier.c18.link_failed')
  showToast({ message: 'لینک باز نشد.', icon: 'alert', action: { label: 'کپی لینک', onClick: () => void copy(url) } })
  return false
}

export function useAfter(ms: number, active: boolean) {
  const [elapsed, setElapsed] = useState(false)
  useEffect(() => {
    if (!active) return setElapsed(false)
    const timer = setTimeout(() => setElapsed(true), ms)
    return () => clearTimeout(timer)
  }, [ms, active])
  return elapsed
}

export function useOnline() {
  const [online, setOnline] = useState(navigator.onLine)
  useEffect(() => {
    const up = () => setOnline(true)
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
    }
  }, [])
  return online
}

// C18.4: skeleton at once, this line after 5 s. The answer or an error comes by 15 s.
export function SlowNote() {
  return (
    <div className="flex flex-none items-center justify-center gap-2" data-testid="slow-note">
      <Spinner />
      <span className="text-sm text-muted-foreground">کمی بیشتر از معمول طول می‌کشد…</span>
    </div>
  )
}

export function Checking() {
  const slow = useAfter(5000, true)
  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="checking">
      <Skeleton className="h-10 w-full flex-none" />
      {slow ? <SlowNote /> : null}
      <Skeleton className="min-h-0 w-full flex-1" />
      <Skeleton className="h-5 w-[60%] flex-none" />
    </div>
  )
}

// C18.1: the data on screen stays.
export function OfflineBar({ onRetry }: { onRetry: () => void }) {
  return (
    <Alert className="flex-none" data-testid="offline-bar">
      <AlertDescription className="flex w-full items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <WifiOff className="size-4 shrink-0" aria-hidden />
          اتصال اینترنت برقرار نیست
        </span>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw />
          تلاش مجدد
        </Button>
      </AlertDescription>
    </Alert>
  )
}

function Retry({ onRetry, children = 'تلاش مجدد' }: { onRetry: () => void; children?: ReactNode }) {
  return (
    <Button className="w-full" onClick={onRetry}>
      <RefreshCw />
      {children}
    </Button>
  )
}

// C18.2
export function ServerErrorScreen({ onRetry }: { onRetry: () => void }) {
  const navigate = useNavigate()
  useEffect(() => track('courier.c18.server_error'), [])
  return (
    <StateScreen icon={ServerCrash} title="مشکلی از طرف ما پیش آمد" testId="server-error">
      <div className="flex w-full flex-col gap-2">
        <Retry onRetry={onRetry} />
        <Button variant="outline" className="w-full" onClick={() => navigate('/me/support')}>
          <LifeBuoy />
          پشتیبانی
        </Button>
      </div>
    </StateScreen>
  )
}

// C18.6
export function NotFoundScreen() {
  const navigate = useNavigate()
  useEffect(() => track('courier.c18.not_found'), [])
  return (
    <StateScreen icon={SearchX} title="این صفحه پیدا نشد" testId="not-found">
      <Button onClick={() => navigate('/')}>
        <Route />
        جستجوی مسیر
      </Button>
    </StateScreen>
  )
}

// The whole-screen answer to a failed read: the code decides, never the text. Screens handle their own codes first.
export function FailedScreen({ code, onRetry }: { code: CourierCode; onRetry: () => void }) {
  switch (code) {
    case 'offline':
    case 'network_error':
    case 'timeout':
      return (
        <StateScreen icon={WifiOff} title="اتصال اینترنت برقرار نیست" testId="offline-screen">
          <Retry onRetry={onRetry} />
        </StateScreen>
      )
    case 'rate_limited':
      return (
        <StateScreen icon={Clock} title="درخواست‌ها زیاد شد" description="چند لحظه دیگر دوباره امتحان کنید." testId="rate-limited">
          <Retry onRetry={onRetry} />
        </StateScreen>
      )
    case 'not_found':
      return <NotFoundScreen />
    default:
      return <ServerErrorScreen onRetry={onRetry} />
  }
}

export const failedScreen = (error: unknown, onRetry: () => void) => <FailedScreen code={errorCode(error)} onRetry={onRetry} />

export function BootOffline() {
  return (
    <StateScreen icon={WifiOff} title="اتصال اینترنت برقرار نیست" testId="offline-screen">
      <Retry onRetry={() => void boot()} />
    </StateScreen>
  )
}

export function BootFailed() {
  useEffect(() => track('courier.c18.server_error'), [])
  return (
    <StateScreen icon={ServerCrash} title="مشکلی از طرف ما پیش آمد" testId="server-error">
      <div className="flex w-full flex-col gap-2">
        <Retry onRetry={() => void boot()} />
        <Button variant="outline" className="w-full" onClick={() => openLink(`https://t.me/${BOT}?start=support`)}>
          <LifeBuoy />
          پشتیبانی
        </Button>
      </div>
    </StateScreen>
  )
}

// C18.3
export function MaintenanceScreen() {
  useEffect(() => track('courier.c18.maintenance'), [])
  return (
    <StateScreen icon={Wrench} title="در حال به‌روزرسانی هستیم" testId="maintenance">
      <Button variant="outline" onClick={() => openLink(`https://t.me/${MAIN_CHANNEL}`)}>
        <Send />
        کانال چمدون
      </Button>
    </StateScreen>
  )
}

// C18.7
export function OutsideScreen() {
  useEffect(() => track('courier.c18.outside_telegram'), [])
  return (
    <StateScreen icon={Send} title="باربری در تلگرام باز می‌شود" testId="outside">
      <Button onClick={() => window.open(reopenUrl(), '_blank', 'noopener')}>
        <ExternalLink />
        باز کردن در تلگرام
      </Button>
    </StateScreen>
  )
}

// C18.10
export function SessionOverScreen() {
  useEffect(() => track('courier.c18.session_expired'), [])
  return (
    <StateScreen icon={KeyRound} title="نشست شما تمام شد" description="مینی‌اپ را از تلگرام دوباره باز کنید." testId="session-over">
      <Button onClick={() => openLink(reopenUrl())}>
        <RefreshCw />
        باز کردن دوباره
      </Button>
    </StateScreen>
  )
}

// C18.11
export function BlockedScreen() {
  useEffect(() => track('courier.c18.user_blocked'), [])
  return (
    <StateScreen icon={Lock} title="دسترسی این حساب محدود شده است" testId="blocked">
      <Button variant="outline" onClick={() => openLink(`https://t.me/${BOT}?start=support`)}>
        <LifeBuoy />
        پشتیبانی
      </Button>
    </StateScreen>
  )
}

// C18.5 and C18.14: no close button. Off for this person, the button loads the old screens.
export function UpdateSheet({ update }: { update: Update }) {
  useEffect(() => {
    if (update) track(update === 'flag_off' ? 'courier.c18.flag_off' : 'courier.c18.version_mismatch')
  }, [update])
  const title = update === 'flag_off' ? 'باربری به‌روز شد' : 'نسخه‌ی تازه آماده است'
  return (
    <AppSheet open={update !== null} onClose={null} label={title} title={title} testId="update-sheet">
      <Button className="w-full" onClick={() => (update === 'flag_off' ? location.replace(legacyUrl()) : location.reload())}>
        <RefreshCw />
        بارگذاری مجدد
      </Button>
    </AppSheet>
  )
}

// C18.12: Telegram's button is from Bot API 6.1. Below it, the page draws its own.
export const hasTelegramBack = () => !getWebApp()?.initData || isVersionAtLeast('6.1')

export async function copyWithToast(text: string, done: string, failed: string) {
  const ok = await copy(text)
  showToast({ message: ok ? done : failed, icon: ok ? 'check' : 'alert' })
  return ok
}

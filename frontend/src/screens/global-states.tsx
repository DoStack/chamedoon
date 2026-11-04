import { ExternalLink, LifeBuoy, RefreshCw, Send, ServerCrash, TimerOff, UserX, WifiOff, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { AppSheet } from '@/components/app-sheet'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { boot, legacyUrl } from '@/lib/access'
import { routeNow } from '@/lib/back'
import { formatDuration } from '@/lib/format'
import { openExternal, reopenUrl, SUPPORT_URL } from '@/lib/links'
import { track } from '@/lib/report'
import { getStartParam, getWebApp } from '@/lib/telegram'

export function useAfter(ms: number, active: boolean) {
  const [elapsed, setElapsed] = useState(false)
  useEffect(() => {
    if (!active) return setElapsed(false)
    const timer = setTimeout(() => setElapsed(true), ms)
    return () => clearTimeout(timer)
  }, [ms, active])
  return elapsed
}

// F25#4
export function SlowNote() {
  return (
    <div className="flex flex-none items-center justify-center gap-2" data-testid="slow-note">
      <Spinner />
      <span className="text-sm text-muted-foreground">کمی بیشتر از معمول طول می‌کشد…</span>
    </div>
  )
}

// F25#4: skeleton at once, the wait message at 3 s. The answer or an error comes by 15 s.
export function CheckingScreen() {
  const slow = useAfter(3000, true)
  return (
    <div className="flex h-full flex-col gap-3 p-4" data-testid="checking">
      <Skeleton className="h-10 w-full flex-none" />
      {slow ? <SlowNote /> : null}
      <Skeleton className="min-h-0 w-full flex-1" />
      <Skeleton className="h-5 w-[60%] flex-none" />
    </div>
  )
}

// F01#11
export function OfflineScreen() {
  return (
    <StateScreen icon={WifiOff} title="اتصال برقرار نشد" description="اتصال اینترنت خود را بررسی کنید." testId="offline-screen">
      <Button onClick={() => { track('retry_tap', { screen: 'boot_offline' }); void boot() }}>
        <RefreshCw />
        تلاش مجدد
      </Button>
    </StateScreen>
  )
}

// F25#2, at the start: retry asks the flag again.
export function BootFailedScreen() {
  useEffect(() => track('server_error_seen'), [])
  return (
    <StateScreen icon={ServerCrash} title="مشکلی از طرف ما پیش آمد" testId="boot-failed">
      <div className="flex w-full flex-col gap-2">
        <Button className="w-full" onClick={() => { track('retry_tap', { screen: 'boot_failed' }); void boot() }}>
          <RefreshCw />
          تلاش مجدد
        </Button>
        <Button variant="outline" className="w-full" onClick={() => { track('support_open', { from: 'boot_failed' }); openExternal(SUPPORT_URL) }}>
          <LifeBuoy />
          پشتیبانی
        </Button>
      </div>
    </StateScreen>
  )
}

// F02#6
export function OutsideScreen() {
  return (
    <StateScreen icon={Send} title="در تلگرام باز کنید" description="چمدون - خانه فقط داخل تلگرام کار می‌کند." testId="outside-screen">
      <Button asChild>
        <a href={reopenUrl('')} onClick={() => track('outside_open')}>
          باز کردن در تلگرام
          <ExternalLink />
        </a>
      </Button>
    </StateScreen>
  )
}

// F02#7. A Mini App cannot relaunch itself, so this opens the bot's link and falls back to closing. Unverified on clients.
export function SessionOverScreen() {
  return (
    <StateScreen icon={TimerOff} title="نشست شما تمام شده است" description="لطفاً چمدون - خانه را دوباره باز کنید." testId="session-over">
      <Button
        onClick={() => {
          track('session_reopen')
          if (!openExternal(reopenUrl(getStartParam()))) getWebApp()?.close?.()
        }}
      >
        <RefreshCw />
        باز کردن دوباره
      </Button>
    </StateScreen>
  )
}

export function SupportButton({ full = true }: { full?: boolean }) {
  return (
    <Button className={full ? 'w-full' : undefined} onClick={() => { track('support_open', { from: 'blocked' }); openExternal(SUPPORT_URL) }}>
      <LifeBuoy />
      پشتیبانی
    </Button>
  )
}

// F02#9
export function BlockedScreen() {
  return (
    <StateScreen icon={UserX} title="حساب شما غیرفعال است" testId="blocked-screen">
      <SupportButton full={false} />
    </StateScreen>
  )
}

function minutesUntil(until: string | null) {
  if (!until) return null
  const minutes = Math.round((new Date(until).getTime() - Date.now()) / 60_000)
  return minutes > 0 ? minutes : null
}

// F25#3 with a time and a city channel when known, F25#9 without. `channel` comes from the saved city.
export function MaintenanceScreen({ until, channelName, channelUrl }: { until: string | null; channelName?: string; channelUrl?: string }) {
  const minutes = minutesUntil(until)
  useEffect(() => track('maintenance_seen'), [])
  return (
    <StateScreen
      icon={Wrench}
      title="در حال به‌روزرسانی هستیم"
      description={minutes ? `حدود ${formatDuration(minutes)} دیگر برمی‌گردیم.` : undefined}
      testId="maintenance-screen"
    >
      <div className="flex w-full flex-col gap-2">
        <Button className="w-full" onClick={() => { track('retry_tap', { screen: 'maintenance' }); void boot() }}>
          <RefreshCw />
          تلاش مجدد
        </Button>
        {channelUrl ? (
          <Button variant="outline" className="w-full" onClick={() => { track('channel_open', { from: 'maintenance' }); openExternal(channelUrl) }}>
            <Send />
            {channelName}
          </Button>
        ) : null}
      </div>
    </StateScreen>
  )
}

// F25#1: follows the browser's online state, so the bar leaves by itself when the connection returns.
export function useOnline() {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => {
      setOnline(true)
      track('online_restored')
    }
    const off = () => {
      setOnline(false)
      track('offline_seen')
    }
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

export function OfflineBar({ onRetry }: { onRetry: () => void }) {
  useEffect(() => track('offline_shown', { screen: routeNow() }), [])
  return (
    <Alert data-testid="offline-bar" className="shrink-0">
      <AlertDescription className="flex w-full items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-medium text-foreground">
          <WifiOff className="size-4" aria-hidden />
          اتصال اینترنت برقرار نیست
        </span>
        <Button variant="outline" size="sm" onClick={() => { track('retry_tap', { screen: routeNow() }); onRetry() }}>
          <RefreshCw />
          تلاش مجدد
        </Button>
      </AlertDescription>
    </Alert>
  )
}

// F25#5. No close button: the reload is the way out. Forms hold it back from P4 on (BRIEF).
export function UpdateSheet({ update }: { update: 'new_version' | 'flag_off' | null }) {
  const open = update !== null
  useEffect(() => {
    if (update) track(update === 'flag_off' ? 'flag_off_seen' : 'new_version_seen')
  }, [update])
  // Off for this person: nothing new is coming, so the sheet says where the button really goes.
  const title = update === 'flag_off' ? 'این نسخه برای شما فعال نیست' : 'نسخه‌ی تازه آماده است'
  return (
    <AppSheet open={open} onClose={null} label={title} title={title} testId="update-sheet">
      {update === 'flag_off' ? (
        <Button className="w-full" onClick={() => { track('legacy_continue'); location.replace(legacyUrl()) }}>
          ادامه در نسخه‌ی قبلی
        </Button>
      ) : (
        <Button className="w-full" onClick={() => { track('update_reload'); location.reload() }}>
          <RefreshCw />
          بارگذاری مجدد
        </Button>
      )}
    </AppSheet>
  )
}

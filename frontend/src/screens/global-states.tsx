import { ExternalLink, LifeBuoy, RefreshCw, Send, TimerOff, UserX, WifiOff, Wrench } from 'lucide-react'
import { useEffect, useState } from 'react'
import { AppSheet } from '@/components/app-sheet'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { boot } from '@/lib/access'
import { formatDuration } from '@/lib/format'
import { openExternal, reopenUrl, SUPPORT_URL } from '@/lib/links'
import { track } from '@/lib/report'
import { getStartParam, getWebApp } from '@/lib/telegram'

export function CheckingScreen() {
  return <div className="h-full" data-testid="checking" />
}

// F01#11
export function OfflineScreen() {
  return (
    <StateScreen icon={WifiOff} title="اتصال برقرار نشد" description="اتصال اینترنت خود را بررسی کنید." testId="offline-screen">
      <Button onClick={() => void boot()}>
        <RefreshCw />
        تلاش مجدد
      </Button>
    </StateScreen>
  )
}

// F02#6
export function OutsideScreen() {
  return (
    <StateScreen icon={Send} title="در تلگرام باز کنید" description="چمدون - خانه فقط داخل تلگرام کار می‌کند." testId="outside-screen">
      <Button asChild>
        <a href={reopenUrl('')}>
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
    <Button className={full ? 'w-full' : undefined} onClick={() => openExternal(SUPPORT_URL)}>
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
        <Button className="w-full" onClick={() => void boot()}>
          <RefreshCw />
          تلاش مجدد
        </Button>
        {channelUrl ? (
          <Button variant="outline" className="w-full" onClick={() => openExternal(channelUrl)}>
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
  return (
    <Alert data-testid="offline-bar" className="shrink-0">
      <AlertDescription className="flex w-full items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-medium text-foreground">
          <WifiOff className="size-4" aria-hidden />
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

// F25#5. No close button: the reload is the way out. Forms hold it back from P4 on (BRIEF).
export function UpdateSheet({ open }: { open: boolean }) {
  useEffect(() => {
    if (open) track('new_version_seen')
  }, [open])
  return (
    <AppSheet open={open} onClose={null} label="نسخه‌ی تازه آماده است" title="نسخه‌ی تازه آماده است" testId="update-sheet">
      <Button className="w-full" onClick={() => window.location.reload()}>
        <RefreshCw />
        بارگذاری مجدد
      </Button>
    </AppSheet>
  )
}

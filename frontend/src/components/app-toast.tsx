import { Bookmark, BookmarkX, CircleAlert, CircleCheck, Info, MapPin, StickyNote, WifiOff, type LucideIcon } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { appDir } from '@/lib/locale'
import { track } from '@/lib/report'

// Same query as useIsPhone. On desktop the sheet is centered, so the bottom corner stays clear.
const PHONE = '(pointer: coarse), (max-width: 767px)'

const ICONS: Record<string, LucideIcon> = { alert: CircleAlert, check: CircleCheck, info: Info, pin: MapPin, offline: WifiOff, bookmark: Bookmark, bookmarkOff: BookmarkX, note: StickyNote }

export type ToastSpec = {
  message: string
  icon?: keyof typeof ICONS
  action?: { label: string; onClick: () => void }
  id?: string
  sticky?: boolean
  overSheet?: boolean // Above an open sheet, where the bottom position would cover its buttons (F11#7).
}

// The F25 toast: one line with an icon and at most one ghost action.
export function showToast({ message, icon, action, id, sticky, overSheet }: ToastSpec) {
  const Icon = icon ? ICONS[icon] : null
  return toast.custom(
    (t) => (
      <div
        role="status"
        data-testid="toast"
        dir={appDir()}
        className="flex min-h-12 w-full items-center justify-between gap-3 rounded-xl bg-popover py-2 ps-4 pe-2 text-sm font-semibold text-popover-foreground shadow-lg ring-1 ring-border"
      >
        <span className="flex min-w-0 items-center gap-2">
          {Icon ? <Icon className="size-[18px] shrink-0" aria-hidden /> : null}
          {message}
        </span>
        {action ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              toast.dismiss(t)
              action.onClick()
            }}
          >
            {action.label}
          </Button>
        ) : null}
      </div>
    ),
    { id, duration: sticky ? Infinity : 5000, unstyled: true, position: overSheet && window.matchMedia(PHONE).matches ? 'top-center' : undefined },
  )
}

export function rateLimitedToast(retry: () => void, id?: string) {
  showToast({ id, message: 'چند لحظه‌ی دیگر دوباره امتحان کنید.', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => { track('retry_tap', { screen: id ?? 'toast' }); retry() } } }) // F25#6
}

export function needsInternetToast() {
  showToast({ message: 'برای این کار به اینترنت نیاز دارید', icon: 'offline' }) // F25#7
}

export function saveFailedToast(retry: () => void, id?: string) {
  showToast({ id, message: 'ذخیره نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => { track('retry_tap', { screen: id ?? 'toast' }); retry() } } }) // F25#10
}

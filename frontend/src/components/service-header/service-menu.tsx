// OWNED PIECE: see ./OWNERS.md. The dropdown card under the header (approved variant A), loaded on first touch.
import { Check, House, Package, type LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState, type RefObject } from 'react'
import type { Locale } from '@/lib/locale'
import { track } from '@/lib/report'
import { getStartParam, getWebApp } from '@/lib/telegram'
import { NAMES, type Service } from './services'

// Each service's SPA entry. A switch is a full page load in the same webview.
const PATHS: Record<Service, string> = { home: '/app/v2/', courier: '/app/v2/courier/' }
const ORDER: Service[] = ['home', 'courier']

const ICONS: Record<Service, LucideIcon> = { home: House, courier: Package }

// Both apps apply the launch link once per launch (`chamedoon.start`, `chamedoon.courier.start`). Marking both as used
// keeps one app's start param from sending the person somewhere when they arrive at the other app.
function markLaunchUsed() {
  const launch = new URLSearchParams(getWebApp()?.initData ?? '').get('auth_date') ?? ''
  const value = `${getStartParam()}|${launch}`
  try {
    sessionStorage.setItem('chamedoon.start', value)
    sessionStorage.setItem('chamedoon.courier.start', value)
  } catch {
    // No storage: the other app may apply the link again, which only repeats a navigation.
  }
}

// Telegram keeps its launch data in sessionStorage and the hash; the `tg*` query keys are the test and preview mocks.
function targetUrl(path: string) {
  const keep = new URLSearchParams()
  new URLSearchParams(location.search).forEach((value, key) => {
    if (key.startsWith('tg')) keep.append(key, value)
  })
  const search = keep.toString()
  return `${path}${search ? `?${search}` : ''}${location.hash}`
}

type Props = { current: Service; locale: Locale; anchor: RefObject<HTMLDivElement>; onClose: () => void }

export default function ServiceMenu({ current, locale, anchor, onClose }: Props) {
  const menu = useRef<HTMLDivElement>(null)
  const [top, setTop] = useState(0)
  const persian = locale === 'fa'

  useEffect(() => {
    const bar = anchor.current?.closest('.app-top') ?? anchor.current
    setTop(bar?.getBoundingClientRect().bottom ?? 0)
    menu.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pick = (id: Service) => {
    onClose()
    if (id === current) return
    track('service_switch', { from: current, to: id })
    markLaunchUsed()
    location.assign(targetUrl(PATHS[id]))
  }

  return (
    <>
      <div aria-hidden className="fixed inset-x-0 bottom-0 z-40 bg-black/30" style={{ top }} onClick={onClose} data-testid="service-scrim" />
      <div
        ref={menu}
        role="menu"
        aria-label={persian ? 'خدمات چمدون' : 'Chamedoon services'}
        className="absolute top-[calc(100%+10px)] left-1/2 z-50 w-62 -translate-x-1/2 rounded-2xl border border-border bg-popover p-1.5 text-popover-foreground shadow-[0_16px_36px_-12px_rgba(24,33,43,.35)]"
        data-testid="service-menu"
      >
        {ORDER.map((id) => {
          const on = id === current
          const Icon = ICONS[id]
          return (
            <button
              key={id}
              type="button"
              role="menuitemradio"
              aria-checked={on}
              onClick={() => pick(id)}
              className={`flex min-h-13 w-full items-center gap-3 rounded-xl px-2.5 py-2 text-start outline-none focus-visible:ring-2 focus-visible:ring-ring ${on ? 'bg-accent' : 'hover:bg-muted'}`}
              data-testid={`service-${id}`}
            >
              <span className={`grid size-9 flex-none place-items-center rounded-[10px] ${on ? 'bg-primary text-primary-foreground' : 'bg-muted text-foreground'}`}>
                <Icon aria-hidden className="size-5" />
              </span>
              <span className="flex-1 text-[15px] font-extrabold">{NAMES[id][locale]}</span>
              <Check aria-hidden className={`size-5 text-primary ${on ? '' : 'invisible'}`} strokeWidth={2.6} />
            </button>
          )
        })}
      </div>
    </>
  )
}

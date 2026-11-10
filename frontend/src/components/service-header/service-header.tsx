// OWNED PIECE: see ./OWNERS.md. Do not edit without the header owner session and Mohammad's approval.
// The Mini App header of every Chamedoon service: the logotype, a rule, the current service and a chevron. Tapping it
// opens a small card under the header that lists the services (./service-menu, loaded on first touch so the first load
// carries only the header); picking another one opens that app in the same webview.
import { lazy, Suspense, useRef, useState } from 'react'
import { getLocale, useLocale, type Locale } from '@/lib/locale'
import { NAMES, type Service } from './services'

const loadMenu = () => import('./service-menu')
const ServiceMenu = lazy(loadMenu)

// `locale` pins the language for an app that has only one (courier is Persian only).
export function ServiceHeader({ current, locale: pinned }: { current: Service; locale?: Locale }) {
  useLocale()
  const locale = pinned ?? getLocale()
  const persian = locale === 'fa'
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const name = NAMES[current][locale]
  const label = persian ? `چمدون - ${name}` : `Chamedoon - ${name}`

  return (
    <div ref={root} className="relative" dir={persian ? 'rtl' : 'ltr'} data-testid="service-header">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onPointerDown={() => void loadMenu()}
        onClick={() => setOpen((value) => !value)}
        className="-my-1 flex h-11 items-center rounded-xl px-2 outline-none [-webkit-tap-highlight-color:transparent] focus-visible:ring-2 focus-visible:ring-ring active:bg-muted"
      >
        <span className="flex items-center gap-1.5" data-testid="app-logo">
          <span className={persian ? 'brand-logo brand-logo-fa h-[26px]' : 'brand-logo brand-logo-en h-5'} />
          <i className="h-[18px] w-px bg-border" />
          <b className="text-[15px] leading-none font-extrabold text-primary">{name}</b>
          <svg aria-hidden viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className={`size-3.5 text-primary transition-transform duration-150 motion-reduce:transition-none ${open ? 'rotate-180' : ''}`}>
            <path d="M4 6l4 4 4-4" />
          </svg>
        </span>
      </button>
      {open ? (
        <Suspense fallback={null}>
          <ServiceMenu current={current} locale={locale} anchor={root} onClose={() => setOpen(false)} />
        </Suspense>
      ) : null}
    </div>
  )
}

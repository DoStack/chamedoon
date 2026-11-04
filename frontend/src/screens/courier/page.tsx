import { useQuery } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { useBack } from '@/lib/back'
import { track } from '@/lib/report'
import { getWebApp } from '@/lib/telegram'
import { courierFetch, unwrap } from './api'
import { CourierNav, type TabId } from './nav'
import { hasTelegramBack } from './states'
import type { Me } from './types'

export const ME_KEY = ['courier', 'me']

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: () => unwrap(courierFetch<Me>('me/')) })
}

// C15: automatic follows Telegram. The choice is the person's, saved on the server, and applied on every launch.
export function applyTheme(theme: string | undefined) {
  const dark = theme === 'dark' || (theme !== 'light' && getWebApp()?.colorScheme === 'dark')
  document.documentElement.classList.toggle('dark', dark)
}

// Back goes up one level; `back={null}` is a root screen, where Telegram's own close takes over.
export function usePageBack(back: string | (() => void) | null) {
  const navigate = useNavigate()
  const handler =
    back === null
      ? null
      : () => {
          track('courier_back_tap', { telegram: hasTelegramBack() })
          if (typeof back === 'function') back()
          else navigate(back)
        }
  useBack(handler)
  return handler
}

// One screen: tabs above the body on desktop, below it on a phone. `tab={null}` hides them, as `c.page(nav=False)` does.
export function Page({ tab, back = null, foot, children, testId }: { tab: TabId | null; back?: string | (() => void) | null; foot?: ReactNode; children: ReactNode; testId?: string }) {
  const onBack = usePageBack(back)
  const me = useMe()
  const count = me.data?.counts?.pending_intros ?? 0
  const theme = me.data?.prefs.theme
  useEffect(() => {
    if (theme) applyTheme(theme)
  }, [theme])
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid={testId}>
      {tab ? <CourierNav place="top" active={tab} count={count} /> : null}
      {onBack && !hasTelegramBack() ? (
        <div className="flex-none px-2 pt-2">
          <Button variant="ghost" size="sm" onClick={onBack} data-testid="page-back">
            <ChevronRight />
            برگشت
          </Button>
        </div>
      ) : null}
      <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
      {foot ? <div className="app-footer">{foot}</div> : null}
      {tab ? <CourierNav place="bottom" active={tab} count={count} /> : null}
    </div>
  )
}

// `c.stack`: the scrolling body. `center` keeps forms and settings in a 560 px column on desktop.
export function Stack({ children, center, gap = 'gap-4' }: { children: ReactNode; center?: boolean; gap?: string }) {
  return <div className={`flex flex-col ${gap} p-4 md:p-6 ${center ? ' mx-auto w-full md:max-w-[560px]' : ''}`}>{children}</div>
}

export function H1({ children }: { children: ReactNode }) {
  return <h1 className="text-[22px] leading-[30px] font-extrabold">{children}</h1>
}

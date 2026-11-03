import { Bell, CircleUser, Plus, Route, type LucideIcon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useIsPhone } from '@/components/app-sheet'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'

export type TabId = 'routes' | 'alerts' | 'post' | 'me'

const TABS: Array<{ id: TabId; icon: LucideIcon; label: string; path: string }> = [
  { id: 'routes', icon: Route, label: 'مسیرها', path: '/' },
  { id: 'alerts', icon: Bell, label: 'گوش به زنگ', path: '/alerts' },
  { id: 'post', icon: Plus, label: 'ثبت', path: '/post' },
  { id: 'me', icon: CircleUser, label: 'حساب کاربری', path: '/me' },
]

const ACTIVE = 'text-muted-foreground data-active:text-primary after:!opacity-0'

// Four tabs of its own, per `CC._nav`. `count`: the red badge on «حساب کاربری», intros waiting for this person's answer.
export function CourierNav({ place, active, count = 0 }: { place: 'top' | 'bottom'; active: TabId; count?: number }) {
  const phone = useIsPhone()
  const navigate = useNavigate()
  if (phone !== (place === 'bottom')) return null
  const open = (tab: (typeof TABS)[number]) => {
    track('courier.nav_tab', { tab: tab.id })
    if (tab.id !== active) navigate(tab.path)
  }
  const mark = (tab: (typeof TABS)[number], className: string) => (
    <span className="relative inline-flex">
      <tab.icon className={className} aria-hidden />
      {tab.id === 'me' && count > 0 ? (
        <Badge variant="destructive" className="absolute -top-1.5 -left-2 !h-4 !min-w-4 !px-1 !text-[10px]" data-testid="pending-badge">
          {fa(count)}
        </Badge>
      ) : null}
    </span>
  )
  if (phone) {
    return (
      <nav aria-label="منوی باربری" className="app-nav-bottom flex-none" data-testid="courier-nav">
        <Tabs value={active} dir="rtl" className="h-full">
          <TabsList variant="line" className="!h-full w-full !p-0">
            {TABS.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id} className={`!h-full !flex-col !gap-1 !text-xs !font-semibold ${ACTIVE}`} onClick={() => open(tab)}>
                {mark(tab, '!size-6')}
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </nav>
    )
  }
  return (
    <nav aria-label="منوی باربری" className="h-12 flex-none border-b border-border bg-card px-4 py-1" data-testid="courier-nav">
      <Tabs value={active} dir="rtl" className="h-full">
        <TabsList variant="line" className="!h-full !justify-start !p-0">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id} className={`!h-full !flex-none !gap-2 !px-3 ${ACTIVE}`} onClick={() => open(tab)}>
              {mark(tab, '!size-[18px]')}
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </nav>
  )
}

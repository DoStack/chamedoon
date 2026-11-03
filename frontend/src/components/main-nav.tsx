import { useQuery } from '@tanstack/react-query'
import { Bookmark, CircleUser, Plus, Search, type LucideIcon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useIsPhone } from '@/components/app-sheet'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ALERTS_KEY, fetchAlerts } from '@/lib/alerts'
import { useFeature, useHasSession } from '@/lib/features'
import { FLAGS } from '@/lib/flags'
import { fa } from '@/lib/format'
import { legacyHomeUrl } from '@/lib/links'
import { appDir, t } from '@/lib/locale'
import { usePosting } from '@/lib/posting'
import { track } from '@/lib/report'

// `path`: the tab has no screen here yet, so it goes to the template page.
type TabId = 'search' | 'saved' | 'post' | 'me'

const TABS: Array<{ id: TabId; icon: LucideIcon; label: string; path?: string; off?: boolean }> = [
  { id: 'search', icon: Search, label: 'جستجو' },
  { id: 'saved', icon: Bookmark, label: 'نشان‌شده‌ها', off: !FLAGS.bookmark },
  { id: 'post', icon: Plus, label: 'ثبت آگهی', path: 'new/' },
  { id: 'me', icon: CircleUser, label: 'حساب کاربری', path: 'mine/' },
]

const ACTIVE = 'text-muted-foreground data-active:text-primary after:!opacity-0'

// The list as it was left, filters and scroll, so moving between tabs loses nothing.
let listSearch = ''
let toList: () => void = () => {}

export function rememberListSearch(search: string) {
  listSearch = search
}

export function goToList() {
  toList()
}

// F17 rules: the saved tab's red badge is new alert listings only. Waits for a session, since browsing never starts one.
function useSavedBadge() {
  const alerts = useFeature('alerts')
  const session = useHasSession()
  const list = useQuery({ queryKey: ALERTS_KEY, queryFn: fetchAlerts, enabled: alerts && session })
  return alerts ? (list.data?.results.reduce((sum, row) => sum + row.new_count, 0) ?? 0) : 0
}

function TabBadge({ count }: { count: number }) {
  if (!count) return null
  return (
    <span data-testid="tab-badge" className="absolute top-0.5 start-[calc(50%+6px)] flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] leading-none font-bold text-white">
      {fa(count)}
    </span>
  )
}

// `_nav` in the design library: below the content on a phone, above it on desktop. In the flow, so it never covers
// anything, and `.app-bottom` under it keeps it above the safe area. Only the screens the flows draw it on render this.
export function MainNav({ place, active = 'search' }: { place: 'top' | 'bottom'; active?: TabId }) {
  const phone = useIsPhone()
  const navigate = useNavigate()
  const badge = useSavedBadge()
  const account = useFeature('account')
  const posting = usePosting()
  toList = () => navigate({ pathname: '/', search: listSearch }, { state: { returning: true } })
  if (phone !== (place === 'bottom')) return null
  const open = (tab: (typeof TABS)[number]) => {
    if (tab.id === active) return
    track('nav_tab', { tab: tab.id })
    if (tab.id === 'me' && account) navigate('/account')
    else if (posting && tab.id === 'post') navigate('/post')
    else if (tab.path) location.assign(legacyHomeUrl(tab.path))
    else if (tab.id === 'saved') navigate('/saved')
    else goToList()
  }
  const shown = TABS.filter((tab) => !tab.off)
  if (phone) {
    return (
      <nav aria-label={t('منوی اصلی')} className="app-nav-bottom flex-none" data-testid="main-nav">
        <Tabs value={active} dir={appDir()} className="h-full">
          <TabsList variant="line" className="!h-full w-full !p-0">
            {shown.map((tab) => (
              <TabsTrigger key={tab.id} value={tab.id} className={`!h-full !flex-col !gap-1 !text-xs !font-semibold ${ACTIVE}`} onClick={() => open(tab)}>
                <tab.icon className="!size-6" aria-hidden />
                {t(tab.label)}
                {tab.id === 'saved' ? <TabBadge count={badge} /> : null}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </nav>
    )
  }
  return (
    <nav aria-label={t('منوی اصلی')} className="h-12 flex-none border-b border-border bg-card px-4 py-1" data-testid="main-nav">
      <Tabs value={active} dir={appDir()} className="h-full">
        <TabsList variant="line" className="!h-full !justify-start !p-0">
          {shown.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id} className={`!h-full !flex-none !gap-2 !px-3 ${ACTIVE}`} onClick={() => open(tab)}>
              <tab.icon className="!size-[18px]" aria-hidden />
              {t(tab.label)}
              {tab.id === 'saved' ? <TabBadge count={badge} /> : null}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </nav>
  )
}

import { Banknote, ExternalLink, LifeBuoy, UserCheck, Video, type LucideIcon } from 'lucide-react'
import { useEffect } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { useBack } from '@/lib/back'
import { useSavedCity } from '@/lib/city'
import { cityLabel } from '@/lib/format'
import { openExternal, SUPPORT_URL } from '@/lib/links'
import { t } from '@/lib/locale'
import { track } from '@/lib/report'

type Tip = [LucideIcon, string, string]

const BASE: Tip[] = [
  [Banknote, 'پیش از دیدن خانه پول نفرستید', 'نه بیعانه، نه پول رزرو. اول خانه را ببینید.'],
  [Video, 'خانه را ببینید', 'حضوری، یا اگر دور هستید با تماس تصویری زنده.'],
  [UserCheck, 'صاحبخانه را بشناسید', 'نام او را با قبض یا سند ملک مقایسه کنید.'],
]

// The official site of the province, where we know it. No law is stated in the app itself. Any other city has only support.
const SITES: Record<string, { label: string; url: string }> = {
  toronto: { label: 'سایت LTB انتاریو', url: 'https://tribunalsontario.ca/ltb/' },
  montreal: { label: 'سایت TAL کبک', url: 'https://www.tal.gouv.qc.ca/' },
}

// F21#4, #6, #7.
export default function SafetyScreen() {
  const city = useSavedCity()
  const navigate = useNavigate()
  const location = useLocation()
  useBack(() => (location.key !== 'default' ? navigate(-1) : navigate('/account', { replace: true })))
  useEffect(() => track('safety_view', { city: city?.slug }), [city?.slug])
  if (!city) return <Navigate to="/account" replace />
  const site = SITES[city.slug]
  const support = (
    <Button variant="outline" className="w-full" onClick={() => { track('support_open', { from: 'safety' }); openExternal(SUPPORT_URL) }}>
      <LifeBuoy />
      {t('پشتیبانی')}
    </Button>
  )
  return (
    <div className="h-full overflow-y-auto" data-testid="safety-screen">
      <div className="mx-auto flex w-full max-w-lg flex-col gap-3 px-4 pt-3 pb-4 md:px-6">
        <h1 className="m-0 text-2xl leading-8 font-extrabold">{t('راهنمای ایمنی {city}', { city: cityLabel(city) })}</h1>
        <Card className="gap-0 divide-y divide-border px-4 py-1" data-testid="safety-tips">
          {BASE.map(([Icon, title, detail]) => (
            <div key={title} className="flex items-start gap-3 py-3">
              <Icon className="mt-0.5 size-[18px] flex-none" aria-hidden />
              <div className="min-w-0">
                <div className="text-sm font-bold">{t(title)}</div>
                <div className="text-[13px] leading-5 text-muted-foreground">{t(detail)}</div>
              </div>
            </div>
          ))}
        </Card>
        {site ? (
          <div className="grid grid-cols-2 gap-3">
            <Button variant="outline" className="w-full min-w-0" data-testid="safety-site" onClick={() => { track('safety_site'); openExternal(site.url) }}>
              <span className="truncate">{t(site.label)}</span>
              <ExternalLink />
            </Button>
            {support}
          </div>
        ) : (
          support
        )}
      </div>
    </div>
  )
}

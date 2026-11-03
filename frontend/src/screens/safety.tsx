import { Banknote, ExternalLink, FileCheck, LifeBuoy, PiggyBank, UserCheck, Video, type LucideIcon } from 'lucide-react'
import { useEffect } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { useBack } from '@/lib/back'
import { useSavedCity } from '@/lib/city'
import { openExternal, SUPPORT_URL } from '@/lib/links'
import { t } from '@/lib/locale'
import { track } from '@/lib/report'

type Tip = [LucideIcon, string, string]

const BASE: Tip[] = [
  [Banknote, 'پیش از دیدن خانه پول نفرستید', 'نه بیعانه، نه پول رزرو. اول خانه را ببینید.'],
  [Video, 'خانه را ببینید', 'حضوری، یا اگر دور هستید با تماس تصویری زنده.'],
  [UserCheck, 'صاحبخانه را بشناسید', 'نام او را با قبض یا سند ملک مقایسه کنید.'],
]

// The legal rule belongs to the province, and only these have one drawn (F21#4, #6). Any other city gets the base tips (F21#7).
const RULES: Record<string, { tips: Tip[]; site: { label: string; url: string } }> = {
  toronto: {
    tips: [
      [FileCheck, 'قرارداد استاندارد بخواهید', 'در انتاریو اجاره باید روی فرم استاندارد باشد.'],
      [PiggyBank, 'پیش‌پرداخت محدود است', 'فقط اجاره‌ی ماه آخر و ودیعه‌ی کلید.'],
    ],
    site: { label: 'سایت LTB انتاریو', url: 'https://tribunalsontario.ca/ltb/' },
  },
  montreal: {
    tips: [[PiggyBank, 'ودیعه مجاز نیست', 'در کبک مالک نمی‌تواند ودیعه بگیرد.']],
    site: { label: 'سایت TAL کبک', url: 'https://www.tal.gouv.qc.ca/' },
  },
}

// F21#4, #6, #7.
export default function SafetyScreen() {
  const city = useSavedCity()
  const navigate = useNavigate()
  const location = useLocation()
  useBack(() => (location.key !== 'default' ? navigate(-1) : navigate('/account', { replace: true })))
  useEffect(() => track('safety_view', { city: city?.slug }), [city?.slug])
  if (!city) return <Navigate to="/account" replace />
  const rule = RULES[city.slug]
  const tips = [...BASE, ...(rule?.tips ?? [])]
  const support = (
    <Button variant="outline" className="w-full" onClick={() => openExternal(SUPPORT_URL)}>
      <LifeBuoy />
      {t('پشتیبانی')}
    </Button>
  )
  return (
    <div className="h-full overflow-y-auto" data-testid="safety-screen">
      <div className="mx-auto flex w-full max-w-lg flex-col gap-3 px-4 pt-3 pb-4 md:px-6">
        <h1 className="m-0 text-2xl leading-8 font-extrabold">{t('راهنمای ایمنی {city}', { city: city.name_fa })}</h1>
        <Card className="gap-0 divide-y divide-border px-4 py-1" data-testid="safety-tips">
          {tips.map(([Icon, title, detail]) => (
            <div key={title} className="flex items-start gap-3 py-3">
              <Icon className="mt-0.5 size-[18px] flex-none" aria-hidden />
              <div className="min-w-0">
                <div className="text-sm font-bold">{t(title)}</div>
                <div className="text-[13px] leading-5 text-muted-foreground">{t(detail)}</div>
              </div>
            </div>
          ))}
        </Card>
        {rule ? (
          <div className="grid grid-cols-2 gap-3">
            <Button variant="outline" className="w-full min-w-0" data-testid="safety-site" onClick={() => openExternal(rule.site.url)}>
              <span className="truncate">{t(rule.site.label)}</span>
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

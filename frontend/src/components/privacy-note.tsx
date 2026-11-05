import { X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { t, useLocale } from '@/lib/locale'
import { track } from '@/lib/report'

export const PRIVACY_TEXT = 'برای بهتر کردن چمدون، کارهایی که در برنامه انجام می‌دهید ثبت می‌شود و ممکن است ضبط شود. شماره تلفن و نام کاربری شما در ضبط‌ها دیده نمی‌شود.'

const SEEN = 'chamedoon.privacy'
let firstOpen: boolean | null = null

// Read once per load, and marked at once: the notice shows on one open only, tapped away or not.
function isFirstOpen() {
  if (firstOpen !== null) return firstOpen
  try {
    firstOpen = !localStorage.getItem(SEEN)
    localStorage.setItem(SEEN, '1')
  } catch {
    firstOpen = true // No storage, so no way to tell: say it rather than not.
  }
  return firstOpen
}

// In the flow under the logo, so it covers nothing on any first screen, a link's listing included.
export function PrivacyNote() {
  useLocale()
  const [open, setOpen] = useState(isFirstOpen)
  useEffect(() => {
    if (open) track('privacy_notice_seen')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  if (!open) return null
  return (
    <div className="flex flex-none items-start gap-2 border-b border-border bg-card py-2 ps-4 pe-2" data-testid="privacy-note">
      <p className="m-0 min-w-0 flex-1 py-1 text-[13px] leading-5">{t(PRIVACY_TEXT)}</p>
      <Button variant="ghost" size="icon" aria-label={t('بستن')} onClick={() => { track('privacy_notice_ok'); setOpen(false) }}>
        <X />
      </Button>
    </div>
  )
}

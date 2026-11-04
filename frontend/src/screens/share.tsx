import { Copy, Send, Share2 } from 'lucide-react'
import { useRef, useState } from 'react'
import type { Listing } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Photo, Price } from '@/components/listing-card'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { cardPlace, cardTitle, currency, fa, roomsLabel } from '@/lib/format'
import { openExternal, shareLink } from '@/lib/links'
import { track } from '@/lib/report'

function shareText(listing: Listing) {
  const price = listing.price_amount != null ? `${fa(listing.price_amount)} ${currency(listing.price_currency)} · ` : ''
  return `${price}${roomsLabel(listing)} · ${cardPlace(listing).split(' · ')[0]}`
}

// F13. The link opens the Mini App on this listing, never the source post, so the receiver meets their own gate.
export function ShareButton({ listing }: { listing: Listing }) {
  const [open, setOpen] = useState(false)
  const field = useRef<HTMLInputElement>(null)
  const link = shareLink(listing)

  const copy = async () => {
    track('share_method', { method: 'copy' })
    const done = await (navigator.clipboard?.writeText(link).then(
      () => true,
      () => false,
    ) ?? Promise.resolve(false))
    if (done) {
      track('share_copied')
      setOpen(false)
      showToast({ message: 'لینک کپی شد', icon: 'check' })
      return
    }
    // F13#4: the clipboard is denied in some WebViews, so the link is selected for copying by hand.
    track('share_copy_failed')
    field.current?.select()
    showToast({ message: 'کپی نشد', icon: 'alert', overSheet: true })
  }

  return (
    <>
      <Button
        variant="outline"
        size="icon"
        aria-label="اشتراک‌گذاری"
        onClick={() => {
          track('share_open', { listing_id: listing.id })
          setOpen(true)
        }}
      >
        <Share2 className="size-5" />
      </Button>
      <AppSheet
        open={open}
        onClose={() => setOpen(false)}
        label="اشتراک‌گذاری"
        title="اشتراک‌گذاری"
        testId="share-sheet"
        foot={
          <div className="flex flex-col gap-2">
            <Button
              className="w-full"
              onClick={() => {
                track('share_method', { method: 'telegram' })
                track('share_sent')
                // Telegram's own send sheet: the message is text and link, with no card (P2-10).
                openExternal(`https://t.me/share/url?url=${encodeURIComponent(link)}&text=${encodeURIComponent(shareText(listing))}`)
              }}
            >
              <Send />
              ارسال در تلگرام
            </Button>
            <Button variant="outline" className="w-full" onClick={() => void copy()}>
              <Copy />
              کپی لینک
            </Button>
          </div>
        }
      >
        <Card className="gap-3 p-4" data-testid="share-preview">
          <div className="flex items-center gap-3">
            <div className="w-28 flex-none">
              <Photo src={listing.thumb} className="rounded-lg" />
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <Price card={listing} />
              <span className="truncate text-sm font-bold">{cardTitle(listing)}</span>
              <span className="truncate text-[13px] text-muted-foreground">{cardPlace(listing)}</span>
            </div>
          </div>
        </Card>
        <Input ref={field} readOnly dir="ltr" aria-label="لینک" value={link.replace('https://', '')} onFocus={(event) => event.target.select()} />
      </AppSheet>
    </>
  )
}

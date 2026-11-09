import { Copy, Images, Send, Share2 } from 'lucide-react'
import { useRef, useState } from 'react'
import type { Listing, Share } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Photo, Price } from '@/components/listing-card'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { meFetch } from '@/lib/api'
import { cardPlace, cardTitle, currency, fa, roomsLabel } from '@/lib/format'
import { openExternal, shareLink } from '@/lib/links'
import { track } from '@/lib/report'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'

const LINK_LINE = 'برای اطلاعات بیشتر و تماس:'

// Until the server answers, or when it cannot: the card's own public fields, never a contact.
function fallbackShare(listing: Listing): Share {
  const link = shareLink(listing)
  const price = listing.price_amount != null ? `${fa(listing.price_amount)} ${currency(listing.price_currency)} · ` : ''
  const text = `${price}${roomsLabel(listing)} · ${cardPlace(listing).split(' · ')[0]}\n\n${LINK_LINE}\n${link}`
  return { text, link, photos: (listing.photos ?? []).map((photo) => photo.url), prepared_id: '' }
}

// t.me/share/url puts the link first by itself, so the text goes without it.
const withoutLink = (share: Share) => share.text.replace(share.link, '').trimEnd()

function savePhoto(url: string, index: number) {
  const app = getWebApp()
  return new Promise<boolean>((resolve) => {
    if (app?.downloadFile && isVersionAtLeast('8.0')) {
      app.downloadFile({ url, file_name: `chamedoon-${index + 1}.jpg` }, (accepted) => resolve(accepted))
      return
    }
    resolve(openExternal(url))
  })
}

// F13. The message is the ad as a group post with the main photo and a link that opens the Mini App on this listing.
// The contact stays behind the receiver's own gate.
export function ShareButton({ listing }: { listing: Listing }) {
  const [open, setOpen] = useState(false)
  const [share, setShare] = useState<Share | null>(null)
  const field = useRef<HTMLTextAreaElement>(null)
  const current = share ?? fallbackShare(listing)

  const load = () => {
    void meFetch<Share>(`/api/v1/me/listings/${listing.id}/share/`, { method: 'POST' }).then((result) => {
      if (result.ok) setShare(result.data)
    })
  }

  const sendByUrl = () => {
    track('share_sent', { listing_id: listing.id, method: 'url' })
    openExternal(`https://t.me/share/url?url=${encodeURIComponent(current.link)}&text=${encodeURIComponent(withoutLink(current))}`)
  }

  const send = () => {
    const app = getWebApp()
    if (current.prepared_id && app?.shareMessage && isVersionAtLeast('8.0')) {
      app.shareMessage(current.prepared_id, (sent) => {
        if (!sent) return
        track('share_sent', { listing_id: listing.id, method: 'prepared' })
        setOpen(false)
      })
      return
    }
    sendByUrl()
  }

  const copy = async () => {
    const done = await (navigator.clipboard?.writeText(current.text).then(
      () => true,
      () => false,
    ) ?? Promise.resolve(false))
    track('share_copy', { listing_id: listing.id, ok: done })
    if (done) {
      setOpen(false)
      showToast({ message: 'متن کپی شد', icon: 'check' })
      return
    }
    // F13#4: the clipboard is denied in some WebViews, so the text is selected for copying by hand.
    field.current?.select()
    showToast({ message: 'کپی نشد', icon: 'alert', overSheet: true })
  }

  const savePhotos = async () => {
    track('share_photos', { listing_id: listing.id, count: current.photos.length })
    // One at a time: Telegram asks about each file, and a second ask while one is open is refused.
    for (const [index, url] of current.photos.entries()) {
      if (!(await savePhoto(url, index))) break
    }
  }

  return (
    <>
      <Button
        variant="outline"
        size="icon"
        aria-label="اشتراک‌گذاری"
        onClick={() => {
          track('share_open', { listing_id: listing.id })
          load()
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
            <Button className="w-full" onClick={send}>
              <Send />
              ارسال در تلگرام
            </Button>
            <Button variant="outline" className="w-full" onClick={() => void copy()}>
              <Copy />
              کپی متن
            </Button>
            {current.photos.length ? (
              <Button variant="outline" className="w-full" onClick={() => void savePhotos()}>
                <Images />
                ذخیره‌ی عکس‌ها
              </Button>
            ) : null}
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
        <textarea
          ref={field}
          readOnly
          rows={6}
          aria-label="متن آگهی"
          data-testid="share-text"
          className="w-full resize-none rounded-lg border border-input bg-transparent px-2.5 py-2 text-[13px] leading-6 outline-none focus-visible:border-ring"
          value={current.text}
          onFocus={(event) => event.target.select()}
        />
      </AppSheet>
    </>
  )
}

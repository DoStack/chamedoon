import { Banknote, CalendarDays, Check, CalendarX, CircleOff, CircleAlert, Clock, ExternalLink, FileCheck, House, ImageOff, MapPin, Pencil, Receipt, RefreshCw, Ruler, SearchX, ShieldAlert, Sofa, User, BedDouble, X } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import type { Availability, Card as CardData, DeepLink, Listing } from '@/api/types'
import { needsInternetToast, rateLimitedToast, saveFailedToast, showToast } from '@/components/app-toast'
import { ListingCard, ON_PHOTO, Photo, Price, wantedWhat } from '@/components/listing-card'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { SlowNote, useAfter } from './global-states'
import { meFetch } from '@/lib/api'
import { useBack } from '@/lib/back'
import { findCity, getSavedCity, saveCity, toSaved, useSavedCity, type SavedCity } from '@/lib/city'
import { useFeed } from '@/lib/feed'
import { cardPlace, currency, fa, formatDate, modeLabel, REGION_RULES, relativeTime, roomsLabel } from '@/lib/format'
import { legacyHomeUrl } from '@/lib/links'
import { overrideCard } from '@/lib/overrides'
import { track } from '@/lib/report'
import { useCities } from '@/lib/use-cities'
import { useListing } from '@/lib/use-listing'
import { cn } from '@/lib/utils'
import { ContactFlow, useContactFlow } from './contact'
import { ShareButton } from './share'

const CLOSED_TEXT: Record<NonNullable<Listing['closed_reason']>, string> = {
  owner: 'این آگهی بسته شد. آگهی‌دهنده خبر داد.',
  users: 'این آگهی بسته شد. کاربران خبر دادند.',
  expired: 'این آگهی بسته شد. مدتی است دیده نشده است.',
}

function Fact({ icon: Icon, label, value }: { icon: typeof Ruler; label: string; value: string }) {
  return (
    <Card className="gap-1 p-3">
      <div className="flex items-center gap-1">
        <Icon className="size-3.5" aria-hidden />
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <b className="text-sm font-bold">{value}</b>
    </Card>
  )
}

const FURNISHED: Partial<Record<Listing['furnished'], string>> = { yes: 'مبله', partial: 'نیمه‌مبله', no: 'بدون مبل' }
const BILLS: Partial<Record<Listing['bills_included'], string>> = { yes: 'داخل قیمت', no: 'جدا' }
const REGISTRATION: Partial<Record<Listing['registration'], string>> = { yes: 'ممکن است', no: 'ممکن نیست' }
const BASIS: Partial<Record<Listing['price_basis'], string>> = { warm: 'گرم', cold: 'سرد' }
const FOR_WHOM: Partial<Record<Listing['roommate_gender'], string>> = { couple: 'زوج', female: 'خانم', male: 'آقا' }

// Unknown values are hidden, so a sparse listing has fewer cells, never a blank one.
function factItems(listing: Listing) {
  const items: Array<{ icon: typeof Ruler; label: string; value: string }> = []
  const push = (icon: typeof Ruler, label: string, value?: string | null) => {
    if (value) items.push({ icon, label, value })
  }
  const german = listing.country === 'DE'
  if (listing.rooms != null) push(BedDouble, german ? 'اتاق' : 'خواب', fa(listing.rooms))
  if (listing.size_m2 != null) push(Ruler, 'متراژ', `${fa(listing.size_m2)} متر`)
  push(Sofa, 'مبلمان', FURNISHED[listing.furnished])
  if (german) {
    push(FileCheck, 'ملد', REGISTRATION[listing.registration])
    push(Receipt, 'اجاره', BASIS[listing.price_basis])
    if (listing.deposit_months != null) push(Banknote, 'ودیعه', `${fa(listing.deposit_months)} ماه`)
  } else {
    push(Receipt, 'قبوض', BILLS[listing.bills_included])
  }
  const dated = listing.available_to != null
  if (listing.available_from) push(CalendarDays, dated ? 'شروع' : 'ورود', formatDate(listing.available_from))
  if (listing.available_to) push(CalendarX, 'پایان', formatDate(listing.available_to))
  if (listing.available_from && listing.available_to && !german) {
    const days = Math.round((new Date(listing.available_to).getTime() - new Date(listing.available_from).getTime()) / 86_400_000)
    push(Clock, 'مدت', listing.price_period === 'night' ? `${fa(days)} شب` : `${fa(Math.max(1, Math.round(days / 30)))} ماه`)
  }
  push(User, 'مناسب', FOR_WHOM[listing.roommate_gender])
  return items
}

function Facts({ items, cols = 3 }: { items: Array<{ icon: typeof Ruler; label: string; value: string }>; cols?: 2 | 3 }) {
  return (
    <div className={cn('grid gap-2', cols === 2 ? 'grid-cols-2' : 'grid-cols-3')} data-testid="facts">
      {items.map((item) => (
        <Fact key={item.label} {...item} />
      ))}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">{title}</h3>
      {children}
    </section>
  )
}

type Answer = 'available' | 'taken'

// F10#9, #18. One answer per person and listing on the server, so a retry is safe.
function AvailabilityAsk({ listing, onGone }: { listing: Listing; onGone: () => void }) {
  const [sending, setSending] = useState<Answer | null>(null)
  const [answered, setAnswered] = useState<Answer | null>(null)
  const [hidden, setHidden] = useState(false)
  if (hidden) return null

  const send = async (answer: Answer) => {
    if (navigator.onLine === false) return needsInternetToast()
    setSending(answer)
    const result = await meFetch<Availability>(`/api/v1/me/listings/${listing.id}/availability/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer }),
    })
    setSending(null)
    if (result.ok) {
      track('availability_answer', { outcome: result.data.outcome })
      if (result.data.outcome === 'noted' || result.data.outcome === 'confirmed') {
        setAnswered(answer)
        showToast({ message: 'ممنون، ثبت شد.', icon: 'check' })
      } else {
        // This answer closed it, or it was closed already. The detail becomes the closed one.
        showToast({ message: 'این آگهی دیگر فعال نیست.', icon: 'info' })
        overrideCard({ ...listing, closed: true })
      }
      return
    }
    switch (result.code) {
      case 'confirm_not_allowed':
        return setHidden(true)
      case 'listing_gone':
        return onGone()
      case 'rate_limited':
        return rateLimitedToast(() => void send(answer))
      // These replace the whole app through onApiFailure.
      case 'maintenance':
      case 'session_expired':
      case 'auth_invalid':
      case 'auth_outside':
      case 'user_blocked':
      case 'miniapp_off':
        return
      default:
        return saveFailedToast(() => void send(answer))
    }
  }

  return (
    <Section title="این آگهی موجود است؟">
      <div className="flex items-center gap-2" data-testid="availability">
        <Button variant={answered === 'available' ? 'secondary' : 'outline'} className="min-w-0 flex-1" disabled={Boolean(sending || answered)} onClick={() => void send('available')}>
          {sending === 'available' ? <Spinner /> : <Check />}
          بله، موجود است
        </Button>
        <Button variant={answered === 'taken' ? 'secondary' : 'outline'} className="min-w-0 flex-1" disabled={Boolean(sending || answered)} onClick={() => void send('taken')}>
          {sending === 'taken' ? <Spinner /> : null}
          نه، بسته شد
        </Button>
      </div>
    </Section>
  )
}

function Fresh({ listing }: { listing: Listing }) {
  return <p className="m-0 text-[13px] leading-[21px] text-muted-foreground">موجود · {relativeTime(listing.last_seen)}</p>
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex-none text-sm text-muted-foreground">{label}</span>
      <span className="text-left text-sm font-semibold">{value}</span>
    </div>
  )
}

function rentalLine(listing: Listing) {
  if (listing.rental_mode === 'long_term') {
    return listing.min_term_months ? `بلندمدت · حداقل ${fa(listing.min_term_months)} ماه` : 'بلندمدت'
  }
  return modeLabel(listing)
}

// F10#3 and #17: full screen, with a thumbnail strip.
function Viewer({ listing, start, onClose }: { listing: Listing; start: number; onClose: () => void }) {
  const [index, setIndex] = useState(start)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useBack(onClose)
  const photo = listing.photos[index]
  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-background" data-testid="viewer" style={{ paddingTop: 'var(--safe-t)', paddingBottom: 'var(--safe-b)' }}>
      <div className="mx-auto flex min-h-0 w-full max-w-[760px] flex-1 flex-col gap-3 p-4 md:p-6">
        <div className="flex flex-none items-center justify-between">
          <Button variant="ghost" size="icon" aria-label="بستن" onClick={onClose}>
            <X className="size-5" />
          </Button>
          <span className="text-sm font-bold">
            {fa(index + 1)} از {fa(listing.photos.length)}
          </span>
        </div>
        <div className="flex min-h-0 flex-1 flex-col justify-center gap-3">
          {failed ? (
            <div className="flex aspect-[4/3] items-center justify-center rounded-none bg-secondary md:rounded-xl" data-testid="viewer-failed">
              <div className="flex flex-col items-center gap-3">
                <ImageOff className="size-6" aria-hidden />
                <span className="text-sm font-medium">عکس بارگذاری نشد</span>
                <Button
                  variant="outline"
                  onClick={() => {
                    setFailed(false)
                    setAttempt(attempt + 1)
                  }}
                >
                  <RefreshCw />
                  تلاش مجدد
                </Button>
              </div>
            </div>
          ) : (
            <img key={`${index}-${attempt}`} src={photo.url} alt="" onError={() => setFailed(true)} className="aspect-[4/3] w-full object-cover md:rounded-xl" data-testid="viewer-photo" />
          )}
          <div className="mx-auto grid w-full max-w-[480px] grid-cols-6 gap-2">
            {listing.photos.slice(0, 6).map((item, i) => (
              <Button
                key={item.thumb + i}
                variant="outline"
                aria-label={`عکس ${fa(i + 1)}`}
                className={cn('h-auto w-full overflow-hidden p-0', i === index && 'border-2 border-primary')}
                onClick={() => {
                  setIndex(i)
                  setFailed(false)
                }}
              >
                <img src={item.thumb} alt="" className="aspect-[4/3] w-full object-cover" />
              </Button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function Gallery({ listing, onOpen, children, dim }: { listing: Listing; onOpen: (index: number) => void; children?: ReactNode; dim?: boolean }) {
  const hasPhotos = listing.photos.length > 0
  return (
    <div className="flex flex-col gap-2">
      <div role={hasPhotos ? 'button' : undefined} tabIndex={hasPhotos ? 0 : undefined} aria-label={hasPhotos ? 'نمایش عکس‌ها' : undefined} onClick={() => hasPhotos && onOpen(0)} className={hasPhotos ? 'cursor-pointer' : undefined}>
        <Photo src={listing.photos[0]?.url} dim={dim} className="md:rounded-xl">
          {children}
          {hasPhotos ? (
            <Badge variant="outline" className={cn('absolute bottom-2.5 left-2.5', ON_PHOTO)}>
              ۱ از {fa(listing.photos.length)}
            </Badge>
          ) : null}
          {hasPhotos ? null : <span className="absolute inset-x-0 top-[calc(50%+24px)] text-center text-sm font-semibold text-foreground">بدون عکس</span>}
        </Photo>
      </div>
      {hasPhotos ? (
        <div className="hidden grid-cols-6 gap-2 md:grid">
          {listing.photos.slice(0, 6).map((item, i) => (
            <button key={item.thumb + i} type="button" aria-label={`عکس ${fa(i + 1)}`} onClick={() => onOpen(i)} className="min-w-0 overflow-hidden rounded-lg">
              <img src={item.thumb} alt="" className="aspect-[4/3] w-full bg-secondary object-cover" />
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}

// Phone: photo on top and a pinned footer. Desktop: two columns, with the actions at the end of the details.
function DetailFrame({ side, children, foot }: { side: ReactNode; children: ReactNode; foot: ReactNode }) {
  return (
    <div className="flex h-full flex-col" data-testid="detail">
      <div className="min-h-0 flex-1 overflow-y-auto md:grid md:grid-cols-2 md:items-start md:gap-6 md:px-6 md:py-5">
        <div className="min-w-0">{side}</div>
        <div className="flex min-w-0 flex-col gap-2 p-4 pt-3 md:p-0">
          {children}
          <div className="hidden md:block">{foot}</div>
        </div>
      </div>
      <div className="app-footer md:hidden">{foot}</div>
    </div>
  )
}

function ContactActions({ label, flow, listing, testId }: { label: string; flow: ReturnType<typeof useContactFlow>; listing: Listing; testId?: string }) {
  return (
    <div className="flex items-center gap-2" data-testid={testId}>
      <Button className="min-w-0 flex-1" disabled={flow.pending} onClick={() => flow.start('contact')}>
        {flow.pending ? <Spinner /> : null}
        {label}
      </Button>
      <ShareButton listing={listing} />
    </div>
  )
}

export function backTarget(listing: { metro_slug: string } | null, saved: SavedCity | null) {
  if (!listing?.metro_slug) return '/'
  return saved?.slug === listing.metro_slug ? '/' : `/?city=${listing.metro_slug}`
}

function useSimilar(listing: Listing) {
  const feed = useFeed(listing.metro_slug, listing.country)
  return useMemo(() => {
    const near = (card: CardData) => Math.abs((card.price_monthly ?? 0) - (listing.price_monthly ?? 0))
    return feed.cards
      .filter((card) => card.id !== listing.id && card.kind === 'offer' && card.property_type === listing.property_type)
      .sort((a, b) => near(a) - near(b))
      .slice(0, 2)
  }, [feed.cards, listing])
}

// F02#3, F10#6: one closed view. No contact and no source post.
function ClosedView({ listing, onBack, onSimilar }: { listing: Listing; onBack: () => void; onSimilar: () => void }) {
  const similar = useSimilar(listing)
  useBack(onBack)
  useEffect(() => track('detail_closed', { reason: listing.closed_reason }), [listing.closed_reason])
  return (
    <DetailFrame
      side={
        <Photo src={listing.photos[0]?.url} dim className="md:rounded-xl">
          <Badge className="absolute top-3 right-3 bg-foreground text-background">بسته شد</Badge>
        </Photo>
      }
      foot={
        <div className="flex items-center gap-2">
          <Button className="min-w-0 flex-1" onClick={onSimilar}>
            دیدن آگهی‌های مشابه
          </Button>
          <ShareButton listing={listing} />
        </div>
      }
    >
      <Alert data-testid="closed-note">
        <CircleOff />
        <AlertDescription className="text-foreground">{listing.closed_reason ? CLOSED_TEXT[listing.closed_reason] : 'این آگهی بسته شد.'}</AlertDescription>
      </Alert>
      <Price card={listing} />
      <p className="m-0 text-sm leading-[22px] text-muted-foreground">
        {roomsLabel(listing)}
        {listing.size_m2 != null ? ` · ${fa(listing.size_m2)} متر` : ''} · {cardPlace(listing)}
      </p>
      {similar.length ? (
        <Section title="آگهی‌های مشابه">
          <div className="grid grid-cols-2 gap-3">
            {similar.map((card) => (
              <ListingCard key={card.id} card={card} />
            ))}
          </div>
        </Section>
      ) : null}
    </DetailFrame>
  )
}

// F10#10, #11
function WantedView({ listing, flow, onBack }: { listing: Listing; flow: ReturnType<typeof useContactFlow>; onBack: () => void }) {
  useBack(onBack)
  const closed = listing.state === 'closed'
  const budget = listing.price_max != null ? ` · بودجه تا ${fa(listing.price_max)} ` : ''
  const title = (
    <>
      دنبال خانه{budget}
      {listing.price_max != null ? <bdi dir="ltr">{currency(listing.price_currency)}</bdi> : null}
    </>
  )
  const items = [
    ...(listing.area ? [{ icon: MapPin, label: 'محله‌ها', value: listing.area }] : []),
    { icon: House, label: 'نوع', value: roomsLabel(listing) },
    ...(listing.available_from ? [{ icon: CalendarDays, label: 'ورود', value: formatDate(listing.available_from) }] : []),
    ...(listing.occupants_wanted ? [{ icon: User, label: 'چند نفر', value: listing.occupants_wanted === 1 ? 'یک نفر' : `${fa(listing.occupants_wanted)} نفر` }] : []),
  ]
  return (
    <div className="flex h-full flex-col" data-testid="wanted-detail">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[560px] flex-col gap-3 p-4 md:p-6">
          {closed ? (
            <>
              <Alert>
                <CircleOff />
                <AlertDescription className="text-foreground">این درخواست بسته شد.</AlertDescription>
              </Alert>
              <h2 className="m-0 text-lg leading-[26px] font-extrabold">{title}</h2>
              <p className="m-0 text-sm text-muted-foreground">
                {wantedWhat(listing)} · {listing.area}
              </p>
            </>
          ) : (
            <>
              <h1 className="m-0 text-2xl leading-8 font-extrabold">{title}</h1>
              <p className="m-0 text-[13px] leading-[21px] text-muted-foreground">{relativeTime(listing.last_seen)}</p>
              <Facts items={items} cols={2} />
              {listing.summary_fa ? (
                <Description text={listing.summary_fa} />
              ) : null}
            </>
          )}
        </div>
      </div>
      <div className="app-footer">
        <div className="mx-auto w-full max-w-lg">
          {closed ? (
            <div className="flex items-center gap-2">
              <Button className="min-w-0 flex-1" onClick={onBack}>
                برگشت به آگهی‌ها
              </Button>
              <ShareButton listing={listing} />
            </div>
          ) : (
            <ContactActions label="تماس" flow={flow} listing={listing} testId="actions" />
          )}
        </div>
      </div>
    </div>
  )
}

// Each paragraph takes its direction from its own text, and a phone number or date stays whole inside either direction.
const NUMBER_RUN = /(\+?[\d۰-۹][\d۰-۹ ().-]{6,24}[\d۰-۹])/

function Description({ text }: { text: string }) {
  return (
    <Section title="توضیحات">
      {text.split(/\n+/).map((line, index) => (
        <p key={index} dir="auto" className="m-0 text-sm leading-[22px]" data-testid="description-line">
          {line.split(NUMBER_RUN).map((part, at) =>
            at % 2 ? (
              <bdi key={at} dir="ltr" className="whitespace-nowrap">
                {part}
              </bdi>
            ) : (
              part
            ),
          )}
        </p>
      ))}
    </Section>
  )
}

function DetailLoading() {
  const slow = useAfter(3000, true)
  return (
    <div className="flex h-full flex-col md:grid md:grid-cols-2 md:gap-6 md:px-6 md:py-5" data-testid="detail-loading">
      <Skeleton className="aspect-[4/3] w-full rounded-none md:rounded-xl" />
      <div className="flex flex-col gap-3 p-4 md:p-0">
        {slow ? <SlowNote /> : null}
        <Skeleton className="h-8 w-[40%]" />
        <Skeleton className="h-6 w-[70%]" />
        <Skeleton className="h-4 w-[55%]" />
        <div className="grid grid-cols-3 gap-2">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      </div>
    </div>
  )
}

function ActiveView({ listing, flow, isOwner, inReview, onBack, onGone }: { listing: Listing; flow: ReturnType<typeof useContactFlow>; isOwner: boolean; inReview: boolean; onBack: () => void; onGone: () => void }) {
  const [viewer, setViewer] = useState<number | null>(null)
  useBack(onBack)
  useEffect(() => track(isOwner ? 'own_listing_view' : 'detail_view'), [isOwner])
  const mode = listing.rental_mode !== 'long_term' ? modeLabel(listing) : ''
  const agent = listing.lister_type === 'agent'
  const rule = REGION_RULES[listing.region]
  const askPrice = listing.price_amount == null

  const foot = isOwner ? (
    <div className="flex items-center gap-2" data-testid="owner-actions">
      <Button asChild className="min-w-0 flex-1">
        <a href={legacyHomeUrl('mine/')}>
          <Pencil />
          ویرایش آگهی
        </a>
      </Button>
      {inReview ? null : <ShareButton listing={listing} />}
    </div>
  ) : (
    <ContactActions label={askPrice ? 'قیمت را بپرسید' : 'تماس'} flow={flow} listing={listing} testId="actions" />
  )

  return (
    <>
      <DetailFrame
        foot={foot}
        side={
          <Gallery listing={listing} onOpen={setViewer}>
            <span className="absolute top-3 right-3 flex items-center gap-1.5">
              <Badge variant="outline" className={ON_PHOTO}>
                {inReview ? 'در انتظار بررسی' : 'موجود'}
              </Badge>
              {isOwner ? (
                <Badge variant="outline" className={ON_PHOTO}>
                  آگهی خودتان
                </Badge>
              ) : null}
              {listing.rental_mode === 'sublet' && listing.country !== 'DE' ? <Badge>{mode}</Badge> : null}
            </span>
          </Gallery>
        }
      >
        <Price card={listing} big />
        <h1 className="m-0 text-2xl leading-8 font-extrabold">
          {roomsLabel(listing)}
          {listing.area ? ` · ${listing.area.split(' · ')[0]}` : ''}
        </h1>
        <p className="m-0 text-sm leading-[22px] text-muted-foreground">{cardPlace(listing)}</p>
        <div className="flex items-center justify-between gap-2">
          <Fresh listing={listing} />
          {inReview ? (
            <span className="flex items-center gap-1 text-[13px] text-muted-foreground">
              <Clock className="size-3.5" aria-hidden />
              دیده نمی‌شود
            </span>
          ) : null}
        </div>
        {mode || agent || listing.wbs_required ? (
          <div className="flex flex-wrap items-center gap-1" data-testid="marks">
            {agent ? <Badge variant="outline">بنگاه</Badge> : null}
            {mode ? <Badge variant="outline">{mode}</Badge> : null}
            {listing.wbs_required ? <Badge variant="outline">نیاز به WBS</Badge> : null}
          </div>
        ) : null}
        <Facts items={factItems(listing)} />
        {listing.rental_mode === 'sublet' && listing.country !== 'DE' ? (
          <Alert data-testid="sublet-note">
            <ShieldAlert />
            <AlertDescription className="text-foreground">قرارداد به نام مستأجر فعلی است. قبل از پرداخت، رضایت کتبی مالک را بخواهید.</AlertDescription>
          </Alert>
        ) : null}
        {isOwner ? null : (
          <>
            {listing.rental_mode === 'long_term' ? (
              <Section title="نوع اجاره">
                <div>
                  <Badge variant="outline">{rentalLine(listing)}</Badge>
                </div>
              </Section>
            ) : null}
            {listing.summary_fa ? (
              <Description text={listing.summary_fa} />
            ) : null}
            <Card className="gap-3 p-4" data-testid="about">
              <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">درباره‌ی این آگهی</h3>
              <Kv label="اولین بار دیده شد" value={relativeTime(listing.first_seen)} />
              {listing.poster_count != null ? <Kv label="آگهی‌دهنده" value={`${fa(listing.poster_count)} آگهی فعال`} /> : null}
              {listing.has_source ? (
                <Button variant="outline" className="w-full" disabled={flow.pending} onClick={() => flow.start('source')}>
                  <ExternalLink />
                  دیدن پست اصلی
                </Button>
              ) : null}
            </Card>
            {rule ? (
              <Alert data-testid="region-rule">
                <ShieldAlert />
                <AlertDescription className="text-foreground">{rule} قبل از دیدن خانه پولی نفرستید.</AlertDescription>
              </Alert>
            ) : null}
          </>
        )}
        {listing.can_confirm ? <AvailabilityAsk listing={listing} onGone={onGone} /> : null}
      </DetailFrame>
      {viewer !== null ? <Viewer listing={listing} start={viewer} onClose={() => setViewer(null)} /> : null}
    </>
  )
}

export function DetailScreen() {
  const params = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const id = Number(params.id)
  const data = useListing(id)
  const saved = useSavedCity()
  const cities = useCities()
  const flow = useContactFlow(data.listing)
  const link = (location.state as { fromLink?: boolean; link?: DeepLink } | null)?.link
  const listing = data.listing
  const [first] = useState(() => !getSavedCity())
  const [goneHere, setGoneHere] = useState(false)

  // In-app arrivals go back in history, so the list keeps its filters and a closed listing is not skipped. A launch has
  // no entry behind it, so it goes to the listing's city.
  const inApp = location.key !== 'default' && !link
  const back = () => (inApp ? navigate(-1) : navigate(listing ? backTarget(listing, getSavedCity()) : '/', { replace: Boolean(link) }))
  const backToList = () => (inApp ? navigate(-1) : navigate('/', { replace: true }))

  // F01#8, F02#8: the city question never comes from a link. A first open takes the listing's city; a return keeps its own.
  useEffect(() => {
    if (!listing || !link || !listing.metro_slug) return
    const found = cities.data ? findCity(cities.data, listing.metro_slug) : null
    if (!found) return
    const current = getSavedCity()
    const name = found.row.name_fa
    if (!current) {
      void saveCity(toSaved(found.country, found.row))
      showToast({
        id: 'link-city',
        message: `شهر شما: ${name}`,
        icon: 'pin',
        action: { label: 'تغییر', onClick: () => navigate('/?change=1') },
      })
    } else if (current.slug !== listing.metro_slug) {
      showToast({
        id: 'link-city',
        message: `در حال دیدن ${name}`,
        icon: 'pin',
        action: { label: `برگشت به ${current.name_fa}`, onClick: () => navigate('/') },
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing?.id, cities.data])

  if (!Number.isInteger(id)) return <GoneScreen first={first} onBack={backToList} />
  if (data.loading) return <DetailLoading />
  if (data.gone || goneHere || flow.step.name === 'gone') return <GoneScreen first={first && !saved} onBack={backToList} />
  if (data.failed || !listing) {
    return <DetailFailed onRetry={data.retry} onBack={backToList} />
  }
  if (listing.kind === 'wanted') {
    return (
      <>
        <WantedView listing={listing} flow={flow} onBack={back} />
        <ContactFlow flow={flow} listing={listing} />
      </>
    )
  }
  if (listing.state === 'closed') return <ClosedView listing={listing} onBack={back} onSimilar={() => navigate(`/?city=${listing.metro_slug}&types=${listing.property_type}`)} />
  return (
    <>
      <ActiveView listing={listing} flow={flow} isOwner={data.isOwner} inReview={data.reviewState !== 'live'} onBack={back} onGone={() => setGoneHere(true)} />
      <ContactFlow flow={flow} listing={listing} />
    </>
  )
}

// F02#4, F01#12
function GoneScreen({ first, onBack }: { first: boolean; onBack: () => void }) {
  useBack(onBack)
  useEffect(() => track('deeplink_gone'), [])
  return (
    <StateScreen icon={SearchX} title="این آگهی دیگر نیست" testId="listing-gone">
      <Button onClick={onBack}>{first ? 'دیدن آگهی‌ها' : 'برگشت به آگهی‌ها'}</Button>
    </StateScreen>
  )
}

// F10#8
function DetailFailed({ onRetry, onBack }: { onRetry: () => void; onBack: () => void }) {
  useBack(onBack)
  useEffect(() => track('detail_failed'), [])
  return (
    <StateScreen icon={CircleAlert} title="آگهی باز نشد" testId="detail-failed">
      <div className="flex w-full flex-col gap-2">
        <Button className="w-full" onClick={onRetry}>
          <RefreshCw />
          تلاش مجدد
        </Button>
        <Button variant="outline" className="w-full" onClick={onBack}>
          برگشت به نتایج
        </Button>
      </div>
    </StateScreen>
  )
}


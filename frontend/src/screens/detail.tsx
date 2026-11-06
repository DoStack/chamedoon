import { Banknote, CalendarDays, CalendarRange, CircleOff, CircleAlert, Clock, FileCheck, House, ImageOff, MapPin, Pencil, Receipt, RefreshCw, Ruler, SearchX, Sofa, User, BedDouble, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom'
import type { Card as CardData, DeepLink, Listing, OwnerStats } from '@/api/types'
import { showToast } from '@/components/app-toast'
import { BookmarkButton } from '@/components/bookmark'
import { OwnerStatsCard, OwnerVerifiedBadge } from '@/components/owner-stats'
import { ListingCard, ON_PHOTO, Photo, Place, PlaceText, Price, wantedWhat } from '@/components/listing-card'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { useIsSaved } from '@/lib/saved'
import { SlowNote, useAfter } from './global-states'
import { useBack } from '@/lib/back'
import { findCity, getSavedCity, saveCity, toSaved, useSavedCity, type SavedCity } from '@/lib/city'
import { useFeed } from '@/lib/feed'
import { availabilityLine, cardPlace, currency, fa, faRooms, formatDate, listingText, modeLabel, placeLine, relativeTime, roomsLabel } from '@/lib/format'
import { legacyHomeUrl } from '@/lib/links'
import { usePosting } from '@/lib/posting'
import { track } from '@/lib/report'
import { useCities } from '@/lib/use-cities'
import { useListing } from '@/lib/use-listing'
import { BARE_SCROLL, SLIDE, STRIP, useStrip } from '@/lib/use-strip'
import { cn } from '@/lib/utils'
import { ContactFlow, useContactFlow } from './contact'
import { ShareButton } from './share'

const CLOSED_TEXT: Record<NonNullable<Listing['closed_reason']>, string> = {
  owner: 'این آگهی بسته شد. آگهی‌دهنده خبر داد.',
  users: 'این آگهی بسته شد. کاربران خبر دادند.',
  expired: 'این آگهی بسته شد. مدتی است دیده نشده است.',
}

function Fact({ icon: Icon, label, value }: { icon: typeof Ruler; label: string; value: React.ReactNode }) {
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
  if (listing.rooms != null) push(BedDouble, german ? 'اتاق' : 'خواب', faRooms(listing.rooms))
  if (listing.size_m2 != null) push(Ruler, 'متراژ', `${fa(listing.size_m2)} متر`)
  push(Sofa, 'مبلمان', FURNISHED[listing.furnished])
  if (german) {
    push(FileCheck, 'ملد', REGISTRATION[listing.registration])
    push(Receipt, 'اجاره', BASIS[listing.price_basis])
    if (listing.deposit_months != null) push(Banknote, 'ودیعه', `${fa(listing.deposit_months)} ماه`)
  } else {
    push(Receipt, 'قبوض', BILLS[listing.bills_included])
  }
  if (listing.min_term_months) push(CalendarRange, 'حداقل', `${fa(listing.min_term_months)} ماه`)
  // The dates themselves are at the top of the page.
  if (listing.available_from && listing.available_to && !german) {
    const days = Math.round((new Date(listing.available_to).getTime() - new Date(listing.available_from).getTime()) / 86_400_000)
    push(Clock, 'مدت', listing.price_period === 'night' ? `${fa(days)} شب` : `${fa(Math.max(1, Math.round(days / 30)))} ماه`)
  }
  push(User, 'مناسب', FOR_WHOM[listing.roommate_gender])
  return items
}

function Facts({ items, cols = 3 }: { items: Array<{ icon: typeof Ruler; label: string; value: React.ReactNode }>; cols?: 2 | 3 }) {
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

// Price at the start, availability at the end. Each keeps to its own side and wraps there, however long, so they cannot touch.
function DetailTop({ listing }: { listing: Listing }) {
  return (
    <div className="flex items-start justify-between gap-4" data-testid="detail-top">
      <div className="min-w-0 flex-1 [overflow-wrap:anywhere]" data-testid="detail-price">
        <Price card={listing} big />
      </div>
      <div className="max-w-[45%] min-w-0 flex-none text-end [overflow-wrap:anywhere]" data-testid="detail-availability">
        <div className="text-sm leading-5 font-bold">{availabilityLine(listing) || 'موجود'}</div>
        <div className="text-xs leading-5 text-muted-foreground">{relativeTime(listing.last_seen)}</div>
      </div>
    </div>
  )
}

// F10#3 and #17: full screen, with a thumbnail strip.
function Viewer({ listing, start, onClose }: { listing: Listing; start: number; onClose: () => void }) {
  const strip = useStrip('viewer', start)
  const [failed, setFailed] = useState<number[]>([])
  const [attempt, setAttempt] = useState(0)
  const thumbs = useRef<HTMLDivElement>(null)
  useBack(onClose)
  useEffect(() => track('photo_view', { index: start, count: listing.photos.length }), [start, listing.photos.length])
  // The current thumbnail stays in view. Measured, so it holds in both directions.
  useEffect(() => {
    const box = thumbs.current
    const item = box?.children[strip.index]
    if (!box || !item) return
    const outer = box.getBoundingClientRect()
    const inner = item.getBoundingClientRect()
    box.scrollBy({ left: inner.left - outer.left - (outer.width - inner.width) / 2 })
  }, [strip.index])
  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-background" data-testid="viewer" style={{ paddingTop: 'var(--safe-t)', paddingBottom: 'var(--safe-b)' }}>
      <div className="mx-auto flex min-h-0 w-full max-w-[760px] flex-1 flex-col gap-3 p-4 md:p-6">
        <div className="flex flex-none items-center justify-between">
          <Button variant="ghost" size="icon" aria-label="بستن" onClick={onClose}>
            <X className="size-5" />
          </Button>
          <span className="text-sm font-bold">
            {fa(strip.index + 1)} از {fa(listing.photos.length)}
          </span>
        </div>
        <div className="flex min-h-0 flex-1 flex-col justify-center gap-3">
          <div ref={strip.ref} {...strip.props} tabIndex={-1} className={cn(STRIP, 'aspect-[4/3] w-full flex-none bg-secondary md:rounded-xl')} data-testid="viewer-strip">
            {listing.photos.map((item, i) => (
              <div key={i} className={SLIDE}>
                {failed.includes(i) ? (
                  i === strip.index ? (
                    <div className="flex size-full items-center justify-center" data-testid="viewer-failed">
                      <div className="flex flex-col items-center gap-3">
                        <ImageOff className="size-6" aria-hidden />
                        <span className="text-sm font-medium">عکس بارگذاری نشد</span>
                        <Button
                          variant="outline"
                          onClick={() => {
                            setFailed((old) => old.filter((at) => at !== i))
                            track('retry_tap', { screen: 'viewer' })
                            setAttempt(attempt + 1)
                          }}
                        >
                          <RefreshCw />
                          تلاش مجدد
                        </Button>
                      </div>
                    </div>
                  ) : null
                ) : strip.shows(i) ? (
                  <img
                    key={attempt}
                    src={item.url}
                    alt=""
                    draggable={false}
                    onLoad={strip.settle}
                    onError={() => {
                      track('photo_failed', { index: i })
                      setFailed((old) => [...old, i])
                      strip.settle()
                    }}
                    className="size-full object-cover"
                    data-testid={i === strip.index ? 'viewer-photo' : undefined}
                  />
                ) : null}
              </div>
            ))}
          </div>
          <div ref={thumbs} className={cn(BARE_SCROLL, 'mx-auto flex w-full max-w-[480px] flex-none gap-2 overflow-x-auto')} data-testid="viewer-thumbs">
            {listing.photos.map((item, i) => (
              <Button
                key={item.thumb + i}
                variant="outline"
                aria-label={`عکس ${fa(i + 1)}`}
                className={cn('h-auto w-[calc((100%-2.5rem)/6)] flex-none overflow-hidden p-0!', i === strip.index && 'border-2 border-primary')}
                onClick={() => {
                  track('photo_swipe', { index: i, screen: 'viewer' })
                  strip.go(i)
                }}
              >
                <img src={item.thumb} alt="" loading="lazy" className="aspect-[4/3] w-full object-cover" />
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
  const [index, setIndex] = useState(0)
  return (
    <div className="flex flex-col gap-2">
      <div role={hasPhotos ? 'button' : undefined} tabIndex={hasPhotos ? 0 : undefined} aria-label={hasPhotos ? 'نمایش عکس‌ها' : undefined} onClick={() => hasPhotos && onOpen(index)} className={hasPhotos ? 'cursor-pointer' : undefined}>
        <Photo srcs={listing.photos.map((photo) => photo.url)} screen="detail" onIndex={setIndex} dim={dim} className="md:rounded-xl">
          {children}
          {hasPhotos ? (
            <Badge variant="outline" className={cn('pointer-events-none absolute bottom-2.5 left-2.5', ON_PHOTO)} data-testid="photo-position">
              {fa(index + 1)} از {fa(listing.photos.length)}
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
      <Button className="min-w-0 flex-1" disabled={flow.pending} onClick={flow.start}>
        {flow.pending ? <Spinner /> : null}
        {label}
      </Button>
      <BookmarkButton id={listing.id} />
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
  const saved = useIsSaved(listing.id)
  useBack(onBack)
  useEffect(() => track('detail_closed', { reason: listing.closed_reason }), [listing.closed_reason])
  return (
    <DetailFrame
      side={
        <Photo srcs={listing.photos.map((photo) => photo.url)} screen="detail" dim className="md:rounded-xl">
          <Badge className="pointer-events-none absolute top-3 right-3 bg-foreground text-background">بسته شد</Badge>
          {saved ? <BookmarkButton id={listing.id} className={cn('absolute top-2 left-2', ON_PHOTO)} /> : null}
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
        {listing.size_m2 != null ? ` · ${fa(listing.size_m2)} متر` : ''} · <PlaceText text={placeLine(listing)} />
      </p>
      {similar.length ? (
        <Section title="آگهی‌های مشابه">
          <div className="grid grid-cols-2 gap-3">
            {similar.map((card) => (
              <ListingCard key={card.id} card={card} from="similar" />
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
    { icon: MapPin, label: listing.area ? 'محله‌ها' : 'شهر', value: <PlaceText text={cardPlace(listing)} /> },
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
                {wantedWhat(listing)} · <PlaceText text={cardPlace(listing)} />
              </p>
            </>
          ) : (
            <>
              <h1 className="m-0 text-2xl leading-8 font-extrabold">{title}</h1>
              <p className="m-0 text-[13px] leading-[21px] text-muted-foreground">{relativeTime(listing.last_seen)}</p>
              <Facts items={items} cols={2} />
              {listingText(listing) ? (
                <Description text={listingText(listing)} />
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
        <p key={index} dir="auto" className="ph-no-capture m-0 text-sm leading-[22px]" data-testid="description-line">
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

function DetailLoading({ onBack }: { onBack: () => void }) {
  useBack(onBack)
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

function ActiveView({ listing, flow, isOwner, stats, inReview, onBack }: { listing: Listing; flow: ReturnType<typeof useContactFlow>; isOwner: boolean; stats: OwnerStats | null; inReview: boolean; onBack: () => void }) {
  const [viewer, setViewer] = useState<number | null>(null)
  const posting = usePosting()
  useBack(onBack)
  useEffect(() => track(isOwner ? 'own_listing_view' : 'detail_view', { listing_id: listing.id }), [isOwner, listing.id])
  const agent = listing.lister_type === 'agent'
  const verified = listing.owner_verified === true
  const askPrice = listing.price_amount == null

  const foot = isOwner ? (
    <div className="flex items-center gap-2" data-testid="owner-actions">
      <Button asChild className="min-w-0 flex-1">
        {posting ? (
          <Link to={`/mine/${listing.id}/edit`} onClick={() => track('mine_edit_tap', { from: 'detail' })}>
            <Pencil />
            ویرایش آگهی
          </Link>
        ) : (
          <a href={legacyHomeUrl('mine/')} onClick={() => track('mine_edit_tap', { from: 'detail' })}>
            <Pencil />
            ویرایش آگهی
          </a>
        )}
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
          <Gallery key={listing.id} listing={listing} onOpen={setViewer}>
            <span className="pointer-events-none absolute top-3 right-3 flex items-center gap-1.5">
              {inReview ? (
                <Badge variant="outline" className={ON_PHOTO}>
                  در انتظار بررسی
                </Badge>
              ) : null}
              {isOwner ? (
                <Badge variant="outline" className={ON_PHOTO}>
                  آگهی خودتان
                </Badge>
              ) : null}
            </span>
          </Gallery>
        }
      >
        <DetailTop listing={listing} />
        <h1 className="m-0 truncate text-lg leading-7 font-extrabold" data-testid="detail-title">
          {roomsLabel(listing)} · {modeLabel(listing) || 'بلندمدت'}
        </h1>
        <div data-testid="detail-place">
          <Place text={placeLine(listing)} />
        </div>
        {inReview ? (
          <span className="flex items-center gap-1 text-[13px] text-muted-foreground">
            <Clock className="size-3.5" aria-hidden />
            دیده نمی‌شود
          </span>
        ) : null}
        {agent || listing.wbs_required || verified ? (
          <div className="flex flex-wrap items-center gap-1" data-testid="marks">
            {verified ? <OwnerVerifiedBadge /> : null}
            {agent ? <Badge variant="outline">بنگاه</Badge> : null}
            {listing.wbs_required ? <Badge variant="outline">نیاز به WBS</Badge> : null}
          </div>
        ) : null}
        {isOwner && stats ? <OwnerStatsCard stats={stats} /> : null}
        <Facts items={factItems(listing)} />
        {!isOwner && listingText(listing) ? <Description text={listingText(listing)} /> : null}
      </DetailFrame>
      {viewer !== null ? <Viewer listing={listing} start={viewer} onClose={() => { track('photo_close'); setViewer(null) }} /> : null}
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

  // In-app arrivals go back in history, so the list keeps its filters and a closed listing is not skipped. A launch has
  // no entry behind it, so it goes to the listing's city.
  const inApp = location.key !== 'default' && !link
  const back = () => {
    track('detail_close', { listing_id: id })
    void (inApp ? navigate(-1) : navigate(listing ? backTarget(listing, getSavedCity()) : '/', { replace: Boolean(link) }))
  }
  const backToList = () => {
    track('detail_close', { listing_id: id, gone: true })
    void (inApp ? navigate(-1) : navigate('/', { replace: true }))
  }

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
        action: { label: 'تغییر', onClick: () => { track('link_city_change'); navigate('/?change=1') } },
      })
    } else if (current.slug !== listing.metro_slug) {
      showToast({
        id: 'link-city',
        message: `در حال دیدن ${name}`,
        icon: 'pin',
        action: { label: `برگشت به ${current.name_fa}`, onClick: () => { track('link_city_back'); navigate('/') } },
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listing?.id, cities.data])

  useEffect(() => {
    if (link) track('listing_open', { listing_id: id, from: 'link' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (!Number.isInteger(id)) return <GoneScreen first={first} onBack={backToList} />
  if (data.loading) return <DetailLoading onBack={back} />
  if (data.gone || flow.step.name === 'gone') return <GoneScreen first={first && !saved} onBack={backToList} />
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
  if (listing.state === 'closed') return <ClosedView listing={listing} onBack={back} onSimilar={() => { track('closed_similar'); navigate(`/?city=${listing.metro_slug}&types=${listing.property_type}`) }} />
  return (
    <>
      <ActiveView listing={listing} flow={flow} isOwner={data.isOwner} stats={data.stats} inReview={data.reviewState !== 'live'} onBack={back} />
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
        <Button className="w-full" onClick={() => { track('retry_tap', { screen: 'detail' }); onRetry() }}>
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


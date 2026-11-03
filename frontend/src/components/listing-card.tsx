import { Image as ImageIcon, Images, MapPin } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { Card as CardData } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cardPlace, cardTitle, currency, fa, genderLabel, isFresh, modeBadge, priceTail, relativeTime, roomsLabel } from '@/lib/format'
import { cn } from '@/lib/utils'

export const ON_PHOTO = 'bg-background'

// A photo that fails to load falls back to the placeholder and hides the count: an optional part never breaks the card.
export function Photo({ src, count, children, dim, className }: { src?: string; count?: string; children?: React.ReactNode; dim?: boolean; className?: string }) {
  const [failed, setFailed] = useState(false)
  const show = src && !failed
  return (
    <div className={cn('relative flex aspect-[4/3] flex-none items-center justify-center overflow-hidden bg-secondary text-muted-foreground', className)}>
      {show ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} data-testid="photo" className={cn('absolute inset-0 size-full object-cover', dim && 'opacity-55')} />
      ) : (
        <ImageIcon className={cn('size-8', dim && 'opacity-55')} aria-hidden data-testid="photo-placeholder" />
      )}
      {children}
      {show && count ? (
        <Badge variant="outline" className={cn('absolute bottom-2.5 left-2.5', ON_PHOTO)}>
          <Images />
          {count}
        </Badge>
      ) : null}
    </div>
  )
}

export function Price({ card, big }: { card: CardData; big?: boolean }) {
  if (card.price_amount == null) {
    return <div className={cn('font-bold text-muted-foreground', big ? 'text-xl' : 'text-base leading-7')}>قیمت: بپرسید</div>
  }
  return (
    <div className={cn('font-extrabold', big ? 'text-[28px] leading-9' : 'text-xl leading-7')}>
      {fa(card.price_amount)} <bdi dir="ltr">{currency(card.price_currency)}</bdi> <small className={cn('font-normal text-muted-foreground', big ? 'text-[15px]' : 'text-sm')}>{priceTail(card)}</small>
    </div>
  )
}

function Place({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-1 text-sm text-muted-foreground">
      <MapPin className="size-3.5 flex-none" aria-hidden />
      <span className="truncate">{text}</span>
    </div>
  )
}

const CARD_LINK = 'grid min-w-0 flex-none rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-ring/50'

export function ListingCard({ card, closed }: { card: CardData; closed?: boolean }) {
  const mode = modeBadge(card)
  const agent = card.lister_type === 'agent'
  return (
    <Link to={`/listing/${card.id}`} className={CARD_LINK} data-testid="listing-card">
      <Card className="h-full gap-0 py-0">
        <Photo src={card.thumb} count={card.photo_count > 0 ? fa(card.photo_count) : undefined} dim={closed}>
          {closed ? (
            <Badge className="absolute top-3 right-3 bg-foreground text-background">بسته شد</Badge>
          ) : isFresh(card) ? (
            <Badge className="absolute top-3 right-3">تازه</Badge>
          ) : null}
        </Photo>
        <div className="flex min-w-0 flex-col gap-1 px-4 pt-3 pb-4">
          <Price card={card} />
          <div className="truncate text-sm font-bold">{cardTitle(card)}</div>
          <Place text={cardPlace(card)} />
          {mode || agent ? (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              {agent ? <Badge variant="outline">بنگاه</Badge> : null}
              {mode ? <Badge variant="outline">{mode}</Badge> : null}
            </div>
          ) : null}
          <div className="truncate text-[13px] text-muted-foreground">
            {closed ? 'بسته شد' : 'موجود'} · {relativeTime(card.last_seen)}
          </div>
        </div>
      </Card>
    </Link>
  )
}

export function wantedWhat(card: CardData) {
  const gender = genderLabel(card)
  return gender ? `${roomsLabel(card)} · ${gender}` : roomsLabel(card)
}

export function WantedCard({ card }: { card: CardData }) {
  return (
    <Link to={`/listing/${card.id}`} className={CARD_LINK} data-testid="wanted-card">
      <Card className="h-full gap-1 p-4">
        <div className="text-base leading-7 font-bold">
          دنبال خانه
          {card.price_max != null ? (
            <>
              {' '}
              · بودجه تا {fa(card.price_max)} <bdi dir="ltr">{currency(card.price_currency)}</bdi>
            </>
          ) : null}
        </div>
        <div className="truncate text-sm font-bold">{wantedWhat(card)}</div>
        <Place text={cardPlace(card)} />
        <div className="truncate text-[13px] text-muted-foreground">{relativeTime(card.last_seen)}</div>
      </Card>
    </Link>
  )
}

export function CardSkeleton() {
  return (
    <div className="grid min-w-0 flex-none" data-testid="card-skeleton">
      <Card className="gap-0 py-0">
        <div className="aspect-[4/3]">
          <Skeleton className="size-full rounded-none" />
        </div>
        <div className="flex flex-col gap-2 px-4 pt-3 pb-4">
          <Skeleton className="h-6 w-[40%]" />
          <Skeleton className="h-4 w-[70%]" />
          <Skeleton className="h-4 w-[55%]" />
        </div>
      </Card>
    </div>
  )
}

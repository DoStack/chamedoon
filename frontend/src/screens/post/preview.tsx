import { Banknote, CalendarDays, House, MapPin, RefreshCw, Search, Sofa, User, type LucideIcon } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { currency, fa, formatDate, modeLabel, roomsLabel, typeLabel } from '@/lib/format'
import { amount, currencyOf, draftCard, type Draft } from '@/lib/post-draft'
import { AppSheet } from '@/components/app-sheet'
import { Bad, FootColumn, StaticCard } from './parts'
import { type Duplicate, type PublishPhase } from './use-publish'

function Line({ icon: Icon, children, bold }: { icon: LucideIcon; children: React.ReactNode; bold?: boolean }) {
  return (
    <div className={`flex items-center gap-2 text-sm leading-[22px]${bold ? ' font-bold' : ''}`}>
      <span className="inline-flex flex-none text-muted-foreground">
        <Icon className="size-4" aria-hidden />
      </span>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

function hashtag(text: string) {
  return `#${text.trim().replace(/[\s·،]+/g, '_')}`
}

const FURNISHED: Record<string, string> = { yes: 'مبله', partial: 'نیمه‌مبله', no: 'بدون مبلمان' }
const WHO: Record<string, string> = { one: 'یک نفر', couple: 'زوج', family: 'خانواده', two_plus: 'دو نفر یا بیشتر' }

function dates(draft: Draft) {
  return [draft.availableFrom ? `از ${formatDate(draft.availableFrom)}` : '', draft.availableTo ? `تا ${formatDate(draft.availableTo)}` : '', draft.minTerm && amount(draft.minTerm) ? `حداقل ${fa(amount(draft.minTerm)!)} ماه` : ''].filter(Boolean).join(' · ')
}

// The channel post as the person will see it. No contact, ever: the reader passes the channel gate to get it (P0-01).
// The description stays out too, so a number typed there cannot reach this screen.
export function ChannelPreview({ draft }: { draft: Draft }) {
  const card = draftCard(draft)
  const money = (
    <>
      {fa(amount(draft.price) ?? 0)} <bdi dir="ltr">{currency(currencyOf(draft.city?.country))}</bdi>
    </>
  )
  const city = draft.city?.name_fa ?? ''
  if (draft.kind === 'wanted') {
    const types = draft.wantedTypes.map((type) => ({ room: 'اتاق', studio: 'استودیو', basement: 'بیسمنت', b1: 'آپارتمان ۱ خوابه', b2: 'آپارتمان ۲ خوابه+', b3: 'آپارتمان ۳ خوابه+', house: 'خانه' })[type])
    return (
      <Card className="gap-2 p-4" data-testid="channel-preview">
        <Line icon={Search} bold>
          دنبال خانه · بودجه تا {money}
        </Line>
        <Line icon={House} bold>
          {[types.join(' یا '), draft.areas.join('، ')].filter(Boolean).join(' · ')}
        </Line>
        <blockquote className="m-0 flex flex-col gap-0.5 border-s-[3px] border-primary ps-3">
          {dates(draft) ? <Line icon={CalendarDays}>{dates(draft)}</Line> : null}
          {draft.household ? <Line icon={User}>{WHO[draft.household]}</Line> : null}
        </blockquote>
        <div className="text-[13px] text-primary">{[city, 'دنبال خانه', ...draft.areas].filter(Boolean).map(hashtag).join(' ')}</div>
      </Card>
    )
  }
  const stayLine = [FURNISHED[draft.furnished] ?? '', draft.bills === 'yes' ? 'قبوض داخل' : ''].filter(Boolean).join(' · ')
  const address = draft.showAddress ? draft.address.trim() : ''
  return (
    <Card className="gap-2 p-3" data-testid="channel-preview">
      <div className="flex items-center gap-3">
        <div className="w-24 flex-none overflow-hidden rounded-lg md:w-32">
          {draft.photos[0] ? <img src={draft.photos[0].thumb} alt="" className="aspect-[4/3] w-full object-cover" /> : <div className="aspect-[4/3] w-full bg-secondary" />}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <Line icon={Banknote} bold>
            {money} {draft.pricePeriod === 'month' ? 'در ماه' : draft.pricePeriod === 'week' ? 'در هفته' : 'هر شب'}
          </Line>
          <Line icon={House} bold>
            {roomsLabel(card)} · {draft.area || city}
          </Line>
        </div>
      </div>
      <blockquote className="m-0 flex flex-col gap-0.5 border-s-[3px] border-primary ps-3">
        {address ? (
          <Line icon={MapPin}>
            <bdi dir="auto">{address}</bdi>
          </Line>
        ) : null}
        {modeLabel(card) ? <Line icon={House}>{modeLabel(card)}</Line> : null}
        {stayLine ? <Line icon={Sofa}>{stayLine}</Line> : null}
        {dates(draft) ? <Line icon={CalendarDays}>{dates(draft)}</Line> : null}
      </blockquote>
      <div className="text-[13px] text-primary">{[city, draft.area, typeLabel(card)].filter(Boolean).map(hashtag).join(' ')}</div>
    </Card>
  )
}

export function PreviewBody({ draft, withCard }: { draft: Draft; withCard: boolean }) {
  const post = (
    <div className="flex min-w-0 flex-col gap-2">
      <p className="m-0 text-[13px] font-bold">پیش‌نمایش پست کانال {draft.city?.name_fa ? `خانه‌ی ${draft.city.name_fa}` : ''}</p>
      <ChannelPreview draft={draft} />
    </div>
  )
  if (!withCard) return post
  return (
    <div className="grid gap-4 md:grid-cols-2 md:gap-6">
      {post}
      <div className="flex min-w-0 flex-col gap-2">
        <p className="m-0 text-[13px] font-bold">کارت در فهرست</p>
        <div className="w-[232px] max-w-full md:w-[316px]">
          <StaticCard card={draftCard(draft)} line="همین الان" />
        </div>
      </div>
    </div>
  )
}

export function FailNote({ phase }: { phase: PublishPhase }) {
  if (phase === 'failed') return <Bad testId="post-failed">آگهی ثبت نشد. متن شما ذخیره است.</Bad>
  if (phase === 'capped')
    return (
      <Bad
        testId="post-cap"
        action={
          <Button variant="outline" size="sm" asChild>
            <Link to="/mine">آگهی‌های من</Link>
          </Button>
        }
      >
        امروز به سقف ثبت آگهی رسیدید. فردا دوباره امتحان کنید.
      </Bad>
    )
  return null
}

export function PublishFoot({ phase, onEdit, onPublish }: { phase: PublishPhase; onEdit: () => void; onPublish: () => void }) {
  const sending = phase === 'sending'
  return (
    <FootColumn>
      <Button variant="outline" className="min-w-0 flex-1" disabled={sending} onClick={onEdit} data-testid="post-edit">
        ویرایش
      </Button>
      <Button className="min-w-0 flex-1" disabled={sending || phase === 'capped'} onClick={onPublish} data-testid="post-publish">
        {sending ? <Spinner /> : phase === 'failed' ? <RefreshCw /> : null}
        {phase === 'failed' ? 'تلاش مجدد' : 'انتشار آگهی'}
      </Button>
    </FootColumn>
  )
}

// F18#16, the person's own; F18#21, someone else's. Says only whose it is: the body holds no contact.
export function DuplicateSheet({ duplicate, onClose, onUpdate, onAnyway, onView }: { duplicate: Duplicate; onClose: () => void; onUpdate: () => void; onAnyway: () => void; onView: () => void }) {
  const mine = duplicate.owner === 'self'
  const listing = duplicate.viewer?.listing ?? null
  return (
    <AppSheet
      open
      onClose={onClose}
      label={mine ? 'این آگهی قبلاً ثبت شده است' : 'آگهی مشابهی در چمدون هست'}
      title={mine ? 'این آگهی قبلاً ثبت شده است' : 'آگهی مشابهی در چمدون هست'}
      testId={mine ? 'dup-own' : 'dup-other'}
      foot={
        <div className="flex flex-col gap-2">
          {mine ? (
            <Button className="w-full" onClick={onUpdate} data-testid="dup-update">
              به‌روز کردن آگهی قبلی
            </Button>
          ) : null}
          <Button variant={mine ? 'outline' : 'default'} className="w-full" onClick={onView} data-testid="dup-view">
            {mine ? 'دیدن آگهی قبلی' : 'دیدن آگهی'}
          </Button>
          <Button variant="outline" className="w-full" onClick={onAnyway} data-testid="dup-anyway">
            {mine ? 'آگهی دیگری است' : 'آگهی من جداست'}
          </Button>
        </div>
      }
    >
      {listing ? (
        <div className="mx-auto w-[300px] max-w-full">
          {mine ? <StaticCard card={listing} badge={<Badge className="absolute top-3 right-3">منتشر شده</Badge>} /> : <StaticCard card={listing} />}
        </div>
      ) : null}
    </AppSheet>
  )
}


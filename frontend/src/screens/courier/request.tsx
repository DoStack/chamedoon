import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AtSign, Banknote, Bell, CalendarDays, CircleAlert, CircleCheck, CircleOff, Clock, ExternalLink, Info, Luggage, MessageCircle, Package, Plane, Plus, RefreshCw, Route, Scale, Send, Share2, Signpost, TimerOff, BellOff, type LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { formatDate } from '@/lib/format'
import { track } from '@/lib/report'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { AlertSheet } from './alert-sheet'
import { courierFetch, CourierError, errorCode, postJson, unwrap, type RouteKeys } from './api'
import { query } from './filters'
import { ageLabel, categoriesLabel, dateLabel, isTrip, nounFor, packageSize, placeKey, routeLabel, sideOfCard, stopsLabel, tripSpace } from './format'
import { ProhibitedButton } from './guide'
import { H1, Page } from './page'
import { Fact, FactGrid, KindBadge, Section, TrustBadge } from './parts'
import { copyWithToast, failedScreen, openLink, requestLink, SlowNote, useAfter } from './states'
import type { components } from '@/api/courier-schema.gen'

type Detail = components['schemas']['RequestDetail']
type Intro = components['schemas']['Intro']
type Published = components['schemas']['PublishedContact']

const PUBLIC_NOTE = 'این آگهی از کانال‌های عمومی آمده و صاحب آن ربات چمدون را باز نکرده است.'

export const requestKey = (id: string | number) => ['courier', 'request', String(id)]

export const routeUrl = (route: RouteKeys) => `/${query({ side: route.side, from: route.origin, to: route.destination })}`

// «مسافرهای همین مسیر» and the alert: every dead end offers both.
export function OtherWay({ icon, title, sub, route, testId, back }: { icon: LucideIcon; title: string; sub?: string; route?: RouteKeys; testId: string; back?: string | (() => void) }) {
  const navigate = useNavigate()
  const [alert, setAlert] = useState(false)
  const noun = route ? nounFor(route.side) : 'مسافر'
  useEffect(() => track('courier_dead_end_shown', { screen: testId }), [testId])
  return (
    <Page tab={null} back={back ?? '/'} testId={testId}>
      <StateScreen icon={icon} title={title} description={sub}>
        <div className="flex w-full flex-col gap-2">
          <Button
            className="w-full"
            onClick={() => {
              track('courier_dead_end_action', { screen: testId, action: 'same_route' })
              navigate(route ? routeUrl(route) : '/')
            }}
            data-testid="same-route"
          >
            <Route />
            {noun === 'مسافر' ? 'مسافرهای همین مسیر' : 'بسته‌های همین مسیر'}
          </Button>
          <Button
            variant="outline"
            className="w-full"
            onClick={() => {
              track('courier_dead_end_action', { screen: testId, action: 'alert' })
              setAlert(true)
            }}
            data-testid="other-alert"
          >
            <Bell />
            گوش به زنگ
          </Button>
        </div>
      </StateScreen>
      <AlertSheet open={alert} onClose={() => setAlert(false)} draft={{ side: route?.side ?? 'DEMAND', from: route?.origin ?? '', to: route?.destination ?? '', month: '', size: '' }} />
    </Page>
  )
}

function keysOf(card: Pick<Detail, 'type' | 'origin' | 'stops'>): RouteKeys {
  return { side: sideOfCard(card), origin: placeKey(card.origin), destination: card.stops[0] ? placeKey(card.stops[0]) : '' }
}

function Loading({ back }: { back: () => void }) {
  const slow = useAfter(5000, true)
  return (
    <Page tab={null} back={back} testId="request-loading">
      <div className="flex flex-col gap-3 p-4 md:grid md:grid-cols-2 md:gap-6 md:p-6">
        <Card className="!gap-2 !p-4">
          <Skeleton className="h-5 w-24" />
          <Skeleton className="h-8 w-[70%]" />
          <Skeleton className="h-4 w-[55%]" />
        </Card>
        <Card className="!gap-2 !p-4">
          <Skeleton className="h-4 w-[70%]" />
          <Skeleton className="h-4 w-[55%]" />
          <Skeleton className="h-4 w-[70%]" />
          <Skeleton className="h-4 w-[40%]" />
        </Card>
        {slow ? <SlowNote /> : null}
      </div>
    </Page>
  )
}

// C05.8. The clipboard is denied in some WebViews: Telegram's share sheet then, and C18.12 says why on an old client.
async function share(id: number) {
  const url = requestLink(id)
  const old = !isVersionAtLeast('6.1')
  const copied = await copyWithToast(url, old ? 'لینک کپی شد. برای اشتراک مستقیم تلگرام را به‌روز کنید.' : 'لینک کپی شد', 'لینک کپی نشد')
  if (!copied && !old) getWebApp()?.openTelegramLink?.(`https://t.me/share/url?url=${encodeURIComponent(url)}`)
}

function Actions({ main, id }: { main: ReactNode; id: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">{main}</div>
      <Button
        variant="outline"
        size="icon"
        aria-label="اشتراک‌گذاری"
        onClick={() => {
          track('courier_request_share', { id })
          void share(id)
        }}
        data-testid="share"
      >
        <Share2 />
      </Button>
    </div>
  )
}

function Summary({ item }: { item: Detail }) {
  return (
    <Card className="!gap-2 !p-4">
      <div className="flex items-center gap-1.5">
        <KindBadge card={item} />
        <TrustBadge count={item.trust_count} />
      </div>
      <H1>
        <bdi>{routeLabel(item)}</bdi>
      </H1>
      <p className="text-[13px] text-muted-foreground">{ageLabel(item.created_at)}</p>
    </Card>
  )
}

function Facts({ item }: { item: Detail }) {
  const trip = isTrip(item)
  return (
    <FactGrid>
      {trip ? <Fact icon={Plane} label="تاریخ پرواز" value={item.date ? formatDate(item.date) : 'نامشخص'} /> : <Fact icon={CalendarDays} label="باید برسد" value={dateLabel(item)} />}
      {trip ? <Fact icon={Luggage} label="جای خالی" value={item.capacity_kg ? tripSpace(item.capacity_kg) : 'نامشخص'} /> : <Fact icon={Scale} label="وزن" value={item.weight_kg ? packageSize(item.weight_kg) : 'نامشخص'} />}
      {item.stops.length > 1 ? <Fact icon={Signpost} label="مقصدها" value={<bdi>{stopsLabel(item.stops)}</bdi>} /> : null}
      <Fact icon={Banknote} label="هزینه" value="توافقی" />
    </FactGrid>
  )
}

function Body({ item, extra }: { item: Detail; extra?: ReactNode }) {
  const what = categoriesLabel(item.categories)
  const refused = [categoriesLabel(item.refused), item.excluded_other_text].filter(Boolean).join('، ')
  return (
    <div className="flex flex-col gap-3">
      <Facts item={item} />
      {what ? (
        <Section title={isTrip(item) ? 'می‌برد' : 'داخل بسته'}>
          <p className="text-sm">{what}</p>
        </Section>
      ) : null}
      {refused ? (
        <Section title="قبول نمی‌کند">
          <p className="ph-no-capture text-sm">{refused}</p>
        </Section>
      ) : null}
      {item.description ? (
        <Section title="توضیح">
          <p className="ph-no-capture text-sm break-words whitespace-pre-line">
            <bdi>{item.description}</bdi>
          </p>
        </Section>
      ) : null}
      <ProhibitedButton />
      {extra}
    </div>
  )
}

// C19.1, C19.11, C19.16, C19.17: the sheet that sends. No own request is allowed; the sheet offers posting first.
function AskSheet({ item, open, failed, sending, onClose, onSend }: { item: Detail; open: boolean; failed: boolean; sending: boolean; onClose: () => void; onSend: () => void }) {
  const navigate = useNavigate()
  const trip = isTrip(item)
  const route = keysOf(item)
  return (
    <AppSheet
      open={open}
      onClose={sending ? null : onClose}
      onBack={onClose}
      label="درخواست معرفی"
      title="درخواست معرفی"
      testId="ask-sheet"
      foot={
        <div className="flex flex-col gap-2">
          <Button className="w-full" disabled={sending} onClick={onSend} data-testid="ask-send">
            {sending ? <Spinner /> : failed ? <RefreshCw /> : <Send />}
            {sending ? 'در حال فرستادن' : failed ? 'تلاش مجدد' : 'فرستادن درخواست'}
          </Button>
          {!item.my_request && !failed ? (
            <Button
              variant="outline"
              className="w-full"
              disabled={sending}
              onClick={() => {
                track('courier_intro_post_first', { id: item.id })
                navigate(`/post/${trip ? 'package' : 'trip'}${query({ from: route.origin, to: route.destination })}`)
              }}
              data-testid="ask-post-first"
            >
              <Plus />
              {trip ? 'اول بسته‌ام را ثبت می‌کنم' : 'اول سفرم را ثبت می‌کنم'}
            </Button>
          ) : null}
        </div>
      }
    >
      {failed ? (
        <Alert variant="destructive" data-testid="ask-failed">
          <CircleAlert />
          <AlertDescription>درخواست فرستاده نشد.</AlertDescription>
        </Alert>
      ) : null}
      <Card className="!gap-3 !p-4">
        <FactGrid>
          <Fact icon={Route} label="مسیر" value={<bdi>{routeLabel(item)}</bdi>} />
          <Fact icon={trip ? Plane : CalendarDays} label={trip ? 'تاریخ پرواز' : 'باید برسد'} value={dateLabel(item)} />
          {item.my_request?.weight_kg ? <Fact icon={Package} label="بسته‌ی شما" value={packageSize(item.my_request.weight_kg)} /> : null}
          {item.my_request?.capacity_kg ? <Fact icon={Luggage} label="جای خالی شما" value={tripSpace(item.my_request.capacity_kg)} /> : null}
        </FactGrid>
      </Card>
      <p className="text-[13px] text-muted-foreground">آیدی‌ها فقط اگر قبول کنند رد و بدل می‌شود.</p>
    </AppSheet>
  )
}

// C18.8: the send may have reached the server. The intro is unique per pair, so reading the request tells.
export function ResultUnknown({ onMine }: { onMine: () => void }) {
  useEffect(() => track('courier_result_unknown_shown'), [])
  return (
    <Page tab={null} testId="result-unknown">
      <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <Spinner />
          اتصال قطع شد. نتیجه را بررسی می‌کنیم…
        </span>
        <Button
          variant="outline"
          className="w-full md:max-w-[320px]"
          onClick={() => {
            track('courier_result_unknown_mine_tap')
            onMine()
          }}
        >
          دیدن درخواست‌های من
        </Button>
      </div>
    </Page>
  )
}

export function RequestScreen() {
  const { id = '' } = useParams()
  const requestId = Number(id) || null
  const navigate = useNavigate()
  const location = useLocation()
  const client = useQueryClient()
  const state = (location.state ?? {}) as { fromLink?: boolean; list?: string; from?: string }
  const detail = useQuery({
    queryKey: requestKey(id),
    queryFn: () => unwrap(courierFetch<Detail>(`requests/${id}/`)),
    refetchOnWindowFocus: true,
    staleTime: 0,
    retry: (count, error) => count < 1 && ['offline', 'network_error', 'server_error'].includes(errorCode(error)),
  })
  const [asking, setAsking] = useState(false)
  const [sendFailed, setSendFailed] = useState(false)
  const [unknown, setUnknown] = useState(false)
  const [cap, setCap] = useState<'self' | 'target' | null>(null)
  const [undelivered, setUndelivered] = useState(false)
  const [alert, setAlert] = useState(false)
  const [published, setPublished] = useState<Published | null>(null)
  const [noPublic, setNoPublic] = useState(false)
  const item = detail.data

  useEffect(() => {
    if (item) track('courier_request_open', { id: item.id, kind: item.type, from: state.fromLink ? 'link' : state.list !== undefined ? 'list' : (state.from ?? 'direct'), reach: item.reach, trust_count: item.trust_count })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id])

  useEffect(() => {
    if (item?.reach === 'intro' && item.intro_status === 'PENDING') track('courier_intro_wait_shown', { id: item.id, intro_id: item.intro_id })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id, item?.reach, item?.intro_status])

  // C01.4: from a channel post, back is the list of that route. From the list, it is the list as it was left.
  const back = () => {
    if (item && state.fromLink) navigate(routeUrl(keysOf(item)), { replace: true })
    else if (state.list !== undefined) navigate(`/${state.list}`)
    else navigate('/')
  }

  // C05.10: only a change seen on return says so, not the first answer.
  const seen = useRef('')
  useEffect(() => {
    if (!item) return
    const { intro_id: _introId, intro_status: _introStatus, ...shown } = item
    const now = JSON.stringify(shown)
    if (seen.current && seen.current !== now) {
      showToast({ message: 'این درخواست تازه شد', icon: 'info' })
      track('courier.c05.detail_edited')
    }
    seen.current = now
  }, [item])

  const code = detail.isError ? errorCode(detail.error) : null
  useEffect(() => {
    if (code) track('courier_request_open_failed', { id: requestId, code })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])
  useEffect(() => {
    if (code === 'own_request') {
      const own = (detail.error as CourierError).failure.requestId
      track('courier.c05.detail_own')
      navigate(own ? `/me/requests/${own}` : '/me/requests', { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  const send = useMutation({
    mutationFn: () => unwrap(postJson<Intro>(`requests/${id}/intro/`)),
    onSuccess: () => {
      setAsking(false)
      setSendFailed(false)
      setUnknown(false)
      showToast({ message: 'درخواست معرفی فرستاده شد', icon: 'check' })
      track('courier.c19.intro_sent')
      track('courier_intro_sent', { id: requestId })
      void client.invalidateQueries({ queryKey: requestKey(id) })
    },
    onError: async (error) => {
      const failure = errorCode(error)
      track(`courier.c19.${failure}`)
      track('courier_intro_ask_failed', { id: requestId, code: failure })
      switch (failure) {
        case 'intro_cap_self':
          setAsking(false)
          return setCap('self')
        case 'intro_cap_target':
          setAsking(false)
          return setCap('target')
        case 'intro_undelivered':
          setAsking(false)
          return setUndelivered(true)
        case 'request_inactive':
        case 'request_date_passed':
        case 'intro_closed':
        case 'intro_unreachable':
        case 'intro_no_public_contact':
        case 'intros_off':
        case 'not_found':
        case 'own_request':
          setAsking(false)
          return void client.invalidateQueries({ queryKey: requestKey(id) })
        case 'offline':
        case 'network_error':
        case 'timeout': {
          setAsking(false)
          setUnknown(true)
          // The request says whether the intro landed.
          const read = await courierFetch<Detail>(`requests/${id}/`)
          setUnknown(false)
          if (read.ok && read.data.intro_id) {
            client.setQueryData(requestKey(id), read.data)
            track('courier_intro_sent', { id: requestId, recovered: true })
            return
          }
          setSendFailed(true)
          setAsking(true)
          return
        }
        default:
          return setSendFailed(true)
      }
    },
  })

  const reveal = useMutation({
    mutationFn: () => unwrap(courierFetch<Published>(`requests/${id}/published/`)),
    onSuccess: (data) => {
      setPublished(data)
      track('courier.c19.intro_imported')
      track('courier_contact_revealed', { id: requestId, surface: 'published', has_handle: Boolean(data.handle), has_source: Boolean(data.source_url) })
    },
    onError: (error) => {
      track('courier_contact_reveal_failed', { id: requestId, code: errorCode(error) })
      if (errorCode(error) === 'intro_no_public_contact') {
        setNoPublic(true)
        return
      }
      showToast({ message: 'راه تماس بارگذاری نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'contact_reveal' }), reveal.mutate()) } })
    },
  })

  if (unknown) return <ResultUnknown onMine={() => navigate('/me/requests')} />
  if (detail.isLoading || code === 'own_request') return <Loading back={back} />
  if (detail.isError && !item) {
    const route = detail.error instanceof CourierError ? detail.error.failure.route : undefined
    if (code === 'request_inactive') return <OtherWay icon={CircleOff} title="این درخواست دیگر فعال نیست" route={route} back={back} testId="request-inactive" />
    if (code === 'request_date_passed') return <OtherWay icon={TimerOff} title="تاریخ این پرواز گذشته است" route={route} back={back} testId="request-date-passed" />
    return (
      <Page tab={null} back={back}>
        {failedScreen(detail.error, () => void detail.refetch())}
      </Page>
    )
  }
  if (!item) return null

  const route = keysOf(item)
  if (undelivered) return <OtherWay icon={BellOff} title="پیام به ایشان نرسید" sub={isTrip(item) ? 'این سفر فعلاً در دسترس نیست.' : 'این بسته فعلاً در دسترس نیست.'} route={route} back={back} testId="intro-undelivered" />
  if (item.reach === 'intro' && item.intro_status === 'DECLINED') return <OtherWay icon={CircleOff} title="معرفی این بار انجام نشد" sub={routeLabel(item)} route={route} back={back} testId="intro-declined" />
  if (item.reach === 'intro' && item.intro_status === 'EXPIRED') return <OtherWay icon={TimerOff} title="این درخواست معرفی منقضی شد" sub="در ۷۲ ساعت پاسخی نیامد." route={route} back={back} testId="intro-unanswered" />

  const alertButton = (
    <Button className="w-full" onClick={() => setAlert(true)} data-testid="request-alert">
      <Bell />
      گوش به زنگ
    </Button>
  )
  let main: ReactNode
  let notice: ReactNode = null
  const none = item.reach === 'none' || noPublic
  if (item.reach === 'off') {
    main = alertButton
    notice = <Note icon={Info} testId="intros-off" text="معرفی دوطرفه موقتاً خاموش است." />
  } else if (none) {
    main = alertButton
    notice = <Note icon={Info} testId="imported-none" text={`${PUBLIC_NOTE} راه تماس عمومی هم ندارد.`} />
  } else if (item.reach === 'published') {
    if (published) {
      const url = published.source_url || (published.handle ? `https://t.me/${published.handle}` : '')
      main = (
        <Button
          className="ph-no-capture w-full"
          onClick={() => {
            track('courier_contact_open_tap', { id: item.id, target: published.source_url ? 'source' : 'handle' })
            openLink(url)
          }}
          data-testid="source-open"
        >
          <ExternalLink />
          {published.source_url ? 'دیدن پست اصلی' : 'پیام در تلگرام'}
        </Button>
      )
      notice = (
        <>
          <Note icon={Info} testId="imported-note" text={PUBLIC_NOTE} />
          {published.handle ? (
            <Card className="!gap-1.5 !p-4" data-testid="published-contact">
              <b className="text-[13px] font-bold">راه تماسی که خودشان منتشر کرده‌اند</b>
              <span className="flex items-center gap-2">
                <AtSign className="size-4" aria-hidden />
                <bdi dir="ltr" className="ph-no-capture text-[15px] font-bold">
                  @{published.handle}
                </bdi>
              </span>
            </Card>
          ) : null}
        </>
      )
    } else {
      main = (
        <Button
          className="w-full"
          disabled={reveal.isPending}
          onClick={() => {
            track('courier_contact_reveal_tap', { id: item.id, surface: 'published' })
            reveal.mutate()
          }}
          data-testid="published-open"
        >
          {reveal.isPending ? <Spinner /> : <ExternalLink />}
          راه تماس منتشرشده
        </Button>
      )
    }
  } else if (item.intro_status === 'PENDING') {
    main = (
      <Button className="w-full" disabled data-testid="intro-waiting">
        <Clock />
        در انتظار پاسخ
      </Button>
    )
  } else if (item.intro_status === 'ACCEPTED') {
    main = (
      <Button
        className="w-full"
        onClick={() => {
          track('courier_contact_reveal_tap', { id: item.id, surface: 'after_accept', intro_id: item.intro_id })
          navigate(`/me/intros/${item.intro_id}`)
        }}
        data-testid="intro-connected"
      >
        <CircleCheck />
        وصل شدید، دیدن راه تماس
      </Button>
    )
  } else {
    main = (
      <Button
        className="w-full"
        disabled={cap !== null}
        onClick={() => {
          setSendFailed(false)
          setAsking(true)
          track('courier.c19.ask')
          track('courier_intro_ask', { id: item.id })
        }}
        data-testid="intro-ask"
      >
        <MessageCircle />
        درخواست معرفی
      </Button>
    )
    if (cap === 'self') notice = <Note icon={Clock} testId="cap-self" text="امروز ۵ درخواست معرفی فرستادید. فردا دوباره امتحان کنید." />
    if (cap === 'target') notice = <Note icon={Clock} testId="cap-target" text="امروز درخواست‌های زیادی برای ایشان رفته است. فردا دوباره امتحان کنید." />
  }

  const foot = <Actions main={main} id={item.id} />
  return (
    <Page tab={null} back={back} foot={<div className="md:hidden">{foot}</div>} testId="request">
      <div className="flex flex-col gap-3 p-4 md:grid md:grid-cols-2 md:items-start md:gap-6 md:px-6 md:py-5">
        <Summary item={item} />
        <div className="flex flex-col gap-3">
          <Body item={item} extra={notice} />
          <div className="mt-3 hidden md:block">{foot}</div>
        </div>
      </div>
      <AskSheet item={item} open={asking} failed={sendFailed} sending={send.isPending}
        onClose={() => {
          track('courier_intro_ask_cancel', { id: item.id })
          setAsking(false)
        }}
        onSend={() => {
          track('courier_intro_ask_send', { id: item.id, retry: sendFailed })
          if (sendFailed) track('courier_retry_tap', { where: 'intro_ask' })
          send.mutate()
        }}
      />
      <AlertSheet open={alert} onClose={() => setAlert(false)} draft={{ side: route.side, from: route.origin, to: route.destination, month: '', size: '' }} />
    </Page>
  )
}

function Note({ icon: Icon, text, testId }: { icon: LucideIcon; text: string; testId: string }) {
  return (
    <Alert data-testid={testId}>
      <Icon />
      <AlertDescription>{text}</AlertDescription>
    </Alert>
  )
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AtSign, Check, CircleCheck, CircleOff, Copy, Info, Package, ShieldCheck, Send, TimerOff, User, CalendarDays } from 'lucide-react'
import { useEffect, useId, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { showToast } from '@/components/app-toast'
import { StateScreen } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldLabel } from '@/components/ui/field'
import { Separator } from '@/components/ui/separator'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { track } from '@/lib/report'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { courierFetch, errorCode, postJson, unwrap } from './api'
import { categoriesLabel, dateLabel, placeKey, routeLabel, sideOfCard, sizeLabel, trustLabel } from './format'
import { FinishButton, NotOpenScreen } from './finish'
import { H1, ME_KEY, Page, Stack } from './page'
import { Fact, FactGrid } from './parts'
import { OtherWay, ResultUnknown, requestKey } from './request'
import { copyWithToast, failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type Intro = components['schemas']['Intro']
type Contact = components['schemas']['Contact']
type Card_ = components['schemas']['RequestCard']

export const introKey = (id: string | number) => ['courier', 'intro', String(id)]

export function MatchHead({ status = 'وصل شدید', icon: Icon = CircleCheck, role, card, meta }: { status?: string; icon?: typeof CircleCheck; role?: string; card: Card_; meta?: string }) {
  return (
    <div className="flex flex-col gap-2" data-testid="match-head">
      <div className="flex items-center gap-1.5">
        <Badge>
          <Icon />
          {status}
        </Badge>
        {role ? <Badge variant="outline">{role}</Badge> : null}
      </div>
      <H1>
        <bdi>{routeLabel(card)}</bdi>
      </H1>
      {meta ? <p className="text-sm text-muted-foreground">{meta}</p> : null}
    </div>
  )
}

// A click with no way to open t.me: Bot API below 6.1, or the call throws. C06.6 copies the username, so the chat is one paste away.
function openChat(url: string) {
  const app = getWebApp()
  if (app?.openTelegramLink && isVersionAtLeast('6.1')) {
    try {
      app.openTelegramLink(url)
      return true
    } catch {
      return false
    }
  }
  return false
}

export const contactDraft = (contact: Contact, card: Card_) => contact.draft || `سلام ${contact.first_name}، از چمدون به شما پیام می‌دهم. درباره‌ی ${routeLabel(card)}.`

// C06.1 to C06.3, and C19.4: name, username or id, and the message ready to send. Only after acceptance.
export function useContactPanel(contact: Contact, card: Card_) {
  const [draft, setDraft] = useState(() => contactDraft(contact, card))
  const [copied, setCopied] = useState(false)
  const fieldId = useId()
  useEffect(() => {
    track('courier_contact_shown', { request_id: card.id, has_username: Boolean(contact.telegram_username), has_telegram_id: Boolean(contact.telegram_user_id), no_username: Boolean(contact.no_username) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.id])
  const copy = async () => {
    const done = await copyWithToast(draft, 'پیام کپی شد', 'پیام کپی نشد؛ متن را انتخاب کنید و خودتان کپی کنید')
    if (done) {
      setCopied(true)
      track('courier.c06.copied')
      track('courier_contact_copy', { request_id: card.id, draft_length: draft.length })
    } else {
      // C06: copy by hand, the text stays selectable.
      const field = document.getElementById(fieldId) as HTMLTextAreaElement | null
      field?.focus()
      field?.select()
      track('courier.c06.copy_failed')
      track('courier_contact_failed', { request_id: card.id, reason: 'copy' })
    }
  }
  const send = async () => {
    track('courier_contact_telegram_tap', { request_id: card.id })
    if (openChat(contact.url)) return
    track('courier.c06.link_failed')
    track('courier_contact_failed', { request_id: card.id, reason: 'telegram', has_username: Boolean(contact.telegram_username) })
    if (contact.telegram_username) await copyWithToast(`@${contact.telegram_username}`, 'تلگرام باز نشد. نام کاربری کپی شد.', 'تلگرام باز نشد')
    else showToast({ message: 'تلگرام باز نشد', icon: 'alert' })
  }
  const body = (
    <>
      <Card className="!gap-3 !p-4" data-testid="contact-card">
        <FactGrid>
          <Fact icon={User} label="نام" value={<span className="ph-no-capture">{contact.first_name || '—'}</span>} />
          {contact.telegram_username ? (
            <Fact
              icon={AtSign}
              label="نام کاربری"
              value={
                <bdi dir="ltr" className="ph-no-capture text-[15px]" data-testid="contact-username">
                  @{contact.telegram_username}
                </bdi>
              }
            />
          ) : null}
          {contact.telegram_user_id ? (
            <Fact
              icon={Send}
              label="شناسه تلگرام"
              value={
                <bdi dir="ltr" className="ph-no-capture text-[15px]" data-testid="contact-id">
                  {contact.telegram_user_id}
                </bdi>
              }
            />
          ) : null}
        </FactGrid>
      </Card>
      {contact.no_username ? (
        <Alert data-testid="no-username">
          <Info />
          <AlertDescription>این کاربر نام کاربری عمومی ندارد. متن زیر را کپی کنید و در چت تلگرام بفرستید.</AlertDescription>
        </Alert>
      ) : null}
      <Field>
        <FieldLabel htmlFor={fieldId}>پیام آماده</FieldLabel>
        <Textarea className="ph-no-capture" id={fieldId} rows={4} value={draft} onChange={(event) => setDraft(event.target.value)} data-testid="contact-draft" />
      </Field>
    </>
  )
  const foot = contact.no_username ? (
    <Button className="w-full" onClick={() => void copy()} data-testid="contact-copy">
      <Copy />
      کپی پیام
    </Button>
  ) : (
    <div className="flex gap-2">
      <Button variant={copied ? 'secondary' : 'outline'} className="flex-1" onClick={() => void copy()} data-testid="contact-copy">
        {copied ? <Check /> : <Copy />}
        {copied ? 'کپی شد' : 'کپی پیام'}
      </Button>
      <Button className="ph-no-capture flex-1" onClick={() => void send()} data-testid="contact-send">
        <Send />
        پیام در تلگرام
      </Button>
    </div>
  )
  return { body, foot }
}

function FormFoot({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-[512px]">{children}</div>
}

function ContactView({ intro, back }: { intro: Intro; back: string }) {
  const card = intro.target_request
  const iAmTraveler = (intro.role === 'target') === (card.type === 'SUPPLY')
  const role = iAmTraveler ? 'شما مسافر هستید' : 'شما فرستنده هستید'
  const { body, foot } = useContactPanel(intro.contact!, card)
  const client = useQueryClient()
  const navigate = useNavigate()
  const [notOpen, setNotOpen] = useState(false)
  useEffect(() => track('courier_intro_contacts_shown', { intro_id: intro.id, role: intro.role, match_id: intro.match_id ?? null }), [intro.id, intro.role, intro.match_id])
  if (notOpen) return <NotOpenScreen matchId={intro.match_id ?? null} card={card} role={role} />
  return (
    <Page tab={null} back={back} foot={<FormFoot>{foot}</FormFoot>} testId="intro-accepted">
      <Stack center>
        <MatchHead card={card} role={role} />
        {body}
        {intro.finish_state === 'open' ? (
          <FinishButton
            path={`me/intros/${intro.id}/complete/`}
            matchId={intro.match_id ?? null}
            onNotOpen={() => setNotOpen(true)}
            onDone={(saved) => {
              void client.invalidateQueries({ queryKey: introKey(intro.id) })
              navigate(`/me/matches/${saved.id}`, { replace: true })
            }}
          />
        ) : null}
      </Stack>
    </Page>
  )
}

// C19.3 and C19.12: what the target decides on. The first name is the only thing about the asker before acceptance.
function Received({ intro, onAnswer, busy }: { intro: Intro; onAnswer: (accept: boolean) => void; busy: boolean }) {
  const theirs = intro.requester_request
  useEffect(() => {
    track('courier_intro_received', { intro_id: intro.id, kind: intro.target_request.type, has_their_request: Boolean(theirs), trust_count: intro.requester_trust_count ?? 0 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intro.id])
  return (
    <Page
      tab={null}
      back="/me/matches"
      testId="intro-received"
      foot={
        <FormFoot>
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" disabled={busy} onClick={() => onAnswer(false)} data-testid="intro-decline">
              نه، ممنون
            </Button>
            <Button className="flex-1" disabled={busy} onClick={() => onAnswer(true)} data-testid="intro-accept">
              {busy ? <Spinner /> : <Check />}
              قبول
            </Button>
          </div>
        </FormFoot>
      }
    >
      <Stack center>
        <H1>درخواست معرفی</H1>
        <Card className="!gap-3 !p-4">
          <div className="ph-no-capture flex items-center gap-2 font-bold">
            <User className="size-[18px] shrink-0" aria-hidden />
            {intro.requester_first_name || 'کسی'} می‌خواهد با شما در ارتباط باشد
          </div>
          <p className="text-sm text-muted-foreground">
            درباره‌ی {intro.target_request.type === 'SUPPLY' ? 'سفر' : 'بسته‌ی'} شما: <bdi>{routeLabel(intro.target_request)}</bdi>
          </p>
          {theirs ? (
            <>
              <Separator />
              <div className="flex flex-col gap-1.5 text-sm" data-testid="intro-their-request">
                <span className="flex items-center gap-1.5">
                  <Package className="size-3.5" aria-hidden />
                  {theirs.type === 'DEMAND' ? 'بسته‌ی ایشان' : 'سفر ایشان'}: {sizeLabel(theirs)}
                  {theirs.categories.length ? ` · ${categoriesLabel(theirs.categories)}` : ''}
                </span>
                <span className="flex items-center gap-1.5">
                  <CalendarDays className="size-3.5" aria-hidden />
                  {dateLabel(theirs)}
                </span>
              </div>
            </>
          ) : null}
          {intro.requester_trust_count ? (
            <span className="flex items-center gap-1.5 text-sm">
              <ShieldCheck className="size-3.5" aria-hidden />
              {trustLabel(intro.requester_trust_count)} در چمدون
            </span>
          ) : null}
        </Card>
        <p className="text-[13px] text-muted-foreground">آیدی‌ها فقط اگر قبول کنید رد و بدل می‌شود.</p>
      </Stack>
    </Page>
  )
}

function ILate({ intro }: { intro: Intro }) {
  const navigate = useNavigate()
  return (
    <Page tab={null} back="/me/matches" testId="intro-late">
      <StateScreen icon={TimerOff} title="این درخواست معرفی منقضی شده است" description={routeLabel(intro.target_request)}>
        <Button
          variant="outline"
          onClick={() => {
            track('courier_intro_late_to_inbox', { intro_id: intro.id })
            navigate('/me/matches')
          }}
        >
          هم‌مسیرها
        </Button>
      </StateScreen>
    </Page>
  )
}

function IDeclined({ intro }: { intro: Intro }) {
  return (
    <Page tab={null} back="/me/matches" testId="intro-i-declined">
      <Stack center>
        <H1>درخواست معرفی</H1>
        <Card className="!gap-2 !p-4">
          <span className="flex items-center gap-2 font-bold">
            <CircleOff className="size-[18px]" aria-hidden />
            رد شد
          </span>
          <p className="text-sm text-muted-foreground">
            درباره‌ی {intro.target_request.type === 'SUPPLY' ? 'سفر' : 'بسته‌ی'} شما: <bdi>{routeLabel(intro.target_request)}</bdi>
          </p>
        </Card>
      </Stack>
    </Page>
  )
}

// /me/intros/:id: C19 by status and side. An unknown id or someone else's intro is C18.6.
export function IntroScreen() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const intro = useQuery({ queryKey: introKey(id), queryFn: () => unwrap(courierFetch<Intro>(`me/intros/${id}/`)), retry: false })
  const [gone, setGone] = useState<'late' | 'inactive' | 'date' | null>(null)
  const [unknown, setUnknown] = useState(false)
  const row = intro.data

  useEffect(() => {
    if (row) track('courier_intro_open', { intro_id: row.id, status: row.status, role: row.role })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row?.id, row?.status])

  useEffect(() => {
    if (intro.isError) track('courier_intro_open_failed', { intro_id: Number(id) || null, code: errorCode(intro.error) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intro.isError])

  useEffect(() => {
    if (row?.role === 'requester' && row.status === 'PENDING') navigate(`/request/${row.target_request.id}`, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row?.role, row?.status])

  // Finished on either side, or in an older screen: the rate form and the scores live on the match (C14.2 to C14.6).
  const finishedMatch = row?.status === 'ACCEPTED' && row.finish_state === 'finished' ? row.match_id : null
  useEffect(() => {
    if (finishedMatch) navigate(`/me/matches/${finishedMatch}`, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finishedMatch])

  const answer = useMutation({
    mutationFn: (accept: boolean) => unwrap(postJson<Intro>(`me/intros/${id}/respond/`, { accept })),
    onSuccess: (data, accept) => {
      client.setQueryData(introKey(id), data)
      void client.invalidateQueries({ queryKey: ME_KEY })
      void client.invalidateQueries({ queryKey: requestKey(data.target_request.id) })
      showToast(accept ? { message: 'آیدی شما برای ایشان فرستاده شد', icon: 'check' } : { message: 'ثبت شد. آیدی شما به ایشان داده نمی‌شود.', icon: 'check' })
      track(accept ? 'courier.c19.intro_accepted' : 'courier.c19.intro_decline_sent')
      track(accept ? 'courier_intro_accept' : 'courier_intro_reject', { intro_id: data.id, role: data.role })
    },
    onError: async (error) => {
      const code = errorCode(error)
      track(`courier.c19.answer_${code}`)
      track('courier_intro_answer_failed', { intro_id: Number(id) || null, code })
      if (code === 'intro_expired') return setGone('late')
      if (code === 'request_inactive') return setGone('inactive')
      if (code === 'request_date_passed') return setGone('date')
      if (code === 'offline' || code === 'network_error' || code === 'timeout') {
        setUnknown(true)
        // Answering twice returns the first answer, so a read is enough to tell.
        await intro.refetch()
        setUnknown(false)
        showToast({ message: 'پاسخ ثبت نشد', icon: 'alert' })
        return
      }
      showToast({ message: 'پاسخ ثبت نشد', icon: 'alert' })
    },
  })

  if (unknown) return <ResultUnknown onMine={() => navigate('/me/matches')} />
  if (intro.isLoading) {
    return (
      <Page tab={null} back="/me/matches" testId="intro-loading">
        <div className="flex h-full items-center justify-center">
          <Spinner />
        </div>
      </Page>
    )
  }
  if (!row) {
    return (
      <Page tab={null} back="/me/matches">
        {failedScreen(intro.error, () => void intro.refetch())}
      </Page>
    )
  }
  const route = { side: sideOfCard(row.target_request), origin: placeKey(row.target_request.origin), destination: row.target_request.stops[0] ? placeKey(row.target_request.stops[0]) : '' }
  if (gone === 'late') return <ILate intro={row} />
  if (gone === 'inactive') return <OtherWay icon={CircleOff} title="این سفر بسته شد" sub={routeLabel(row.target_request)} route={route} back="/me/matches" testId="intro-target-gone" />
  if (gone === 'date') return <OtherWay icon={TimerOff} title="تاریخ این پرواز گذشته است" sub={routeLabel(row.target_request)} route={route} back="/me/matches" testId="intro-date-passed" />
  if (row.status === 'ACCEPTED' && row.contact) return <ContactView intro={row} back="/me/matches" />
  if (row.role === 'target') {
    if (row.status === 'PENDING') return <Received intro={row} busy={answer.isPending} onAnswer={(accept) => (track('courier_intro_answer_tap', { intro_id: row.id, accept }), answer.mutate(accept))} />
    if (row.status === 'DECLINED') return <IDeclined intro={row} />
    return <ILate intro={row} />
  }
  if (row.status === 'DECLINED') return <OtherWay icon={CircleOff} title="معرفی این بار انجام نشد" sub={routeLabel(row.target_request)} route={route} back="/me/matches" testId="intro-declined" />
  if (row.status === 'EXPIRED') return <OtherWay icon={TimerOff} title="این درخواست معرفی منقضی شد" sub="در ۷۲ ساعت پاسخی نیامد." route={route} back="/me/matches" testId="intro-unanswered" />
  // Only with courier_intro_everywhere on, and the contract gains the value with that PR.
  if ((row.status as string) === 'UNDELIVERED') return <OtherWay icon={CircleOff} title="پیام به ایشان نرسید" route={route} back="/me/matches" testId="intro-undelivered" />
  return null
}

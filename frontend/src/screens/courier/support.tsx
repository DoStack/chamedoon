import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, Info, LifeBuoy, Plus, Send } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { showToast } from '@/components/app-toast'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { relativeTime } from '@/lib/format'
import { track } from '@/lib/report'
import { courierFetch, errorCode, newIdempotencyKey, postJson, unwrap } from './api'
import { H1, Page, Stack } from './page'
import { failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type Tickets = components['schemas']['Tickets']
type Thread = components['schemas']['TicketThread']

const TICKETS_KEY = ['courier', 'tickets']
const ticketKey = (id: string | number) => ['courier', 'ticket', String(id)]

const SUBJECTS: Record<string, string> = {
  MATCH_PROBLEM: 'مشکل تطبیق',
  CANNOT_CONTACT_USER: 'عدم امکان تماس با کاربر',
  REPORT_USER: 'گزارش کاربر',
  TECHNICAL_ISSUE: 'مشکل فنی',
  OTHER: 'سایر',
}

const isClosed = (status: string) => status === 'CLOSED'

function TicketBadge({ status }: { status: string }) {
  return isClosed(status) ? <Badge variant="outline">بسته</Badge> : <Badge>باز</Badge>
}

// C16.1
export function TicketsScreen() {
  const navigate = useNavigate()
  const tickets = useQuery({ queryKey: TICKETS_KEY, queryFn: () => unwrap(courierFetch<Tickets>('me/tickets/')) })
  const rows = tickets.data?.items ?? []
  const create = (
    <Button className="w-full" onClick={() => navigate('/me/support/new')} data-testid="ticket-new">
      <Plus />
      ایجاد تیکت پشتیبانی
    </Button>
  )
  return (
    <Page tab="me" back="/me" testId="tickets">
      <Stack center gap="gap-3">
        <H1>تیکت‌های پشتیبانی من</H1>
        {tickets.isLoading ? (
          <>
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </>
        ) : tickets.isError ? (
          failedScreen(tickets.error, () => void tickets.refetch())
        ) : (
          <>
            {rows.length ? null : <StateBox icon={LifeBuoy} title="هنوز تیکتی ندارید" testId="tickets-empty" />}
            {rows.map((row) => (
              <Card key={row.id} className="cursor-pointer !p-4" role="link" tabIndex={0} onClick={() => navigate(`/me/support/${row.id}`)} onKeyDown={(event) => event.key === 'Enter' && navigate(`/me/support/${row.id}`)} data-testid="ticket-row">
                <div className="flex items-center gap-2">
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <h3 className="truncate font-bold">{SUBJECTS[row.subject] ?? SUBJECTS.OTHER}</h3>
                    <p className="truncate text-[13px] text-muted-foreground">{relativeTime(row.updated_at)}</p>
                  </div>
                  <TicketBadge status={row.status} />
                  <ChevronLeft className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                </div>
              </Card>
            ))}
            {create}
          </>
        )}
      </Stack>
    </Page>
  )
}

// C16.2 and C16.5
export function NewTicketScreen() {
  const navigate = useNavigate()
  const client = useQueryClient()
  const [subject, setSubject] = useState('')
  const [message, setMessage] = useState('')
  const [tried, setTried] = useState(false)
  const key = useRef(newIdempotencyKey())
  const send = useMutation({
    mutationFn: () => unwrap(postJson<Thread>('me/tickets/', { subject, message }, { key: key.current })),
    onSuccess: (saved) => {
      void client.invalidateQueries({ queryKey: TICKETS_KEY })
      client.setQueryData(ticketKey(saved.id), saved)
      track('courier.c16.ticket_created')
      navigate(`/me/support/${saved.id}`, { replace: true })
    },
    onError: (error) => {
      track(`courier.c16.create_${errorCode(error)}`)
      if (errorCode(error) === 'ticket_invalid') return setTried(true)
      showToast({ message: 'ارسال نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => send.mutate() } })
    },
  })
  const noSubject = tried && !subject
  const noMessage = tried && !message.trim()
  return (
    <Page
      tab={null}
      back="/me/support"
      testId="ticket-create"
      foot={
        <div className="mx-auto w-full max-w-[512px]">
          <Button
            className="w-full"
            disabled={send.isPending}
            data-testid="ticket-send"
            onClick={() => {
              setTried(true)
              if (subject && message.trim()) send.mutate()
            }}
          >
            {send.isPending ? <Spinner /> : null}
            ارسال
          </Button>
        </div>
      }
    >
      <Stack center>
        <H1>ایجاد تیکت پشتیبانی</H1>
        <Field data-invalid={noSubject || undefined}>
          <FieldLabel htmlFor="ticket-subject">موضوع</FieldLabel>
          <NativeSelect id="ticket-subject" dir="rtl" className="w-full" value={subject} onChange={(event) => setSubject(event.target.value)} data-testid="ticket-subject">
            <NativeSelectOption value="">یک موضوع انتخاب کنید</NativeSelectOption>
            {Object.entries(SUBJECTS).map(([code, label]) => (
              <NativeSelectOption key={code} value={code}>
                {label}
              </NativeSelectOption>
            ))}
          </NativeSelect>
          {noSubject ? <FieldError data-testid="ticket-no-subject">یک موضوع انتخاب کنید.</FieldError> : null}
        </Field>
        <Field data-invalid={noMessage || undefined}>
          <FieldLabel htmlFor="ticket-message">پیام</FieldLabel>
          <Textarea id="ticket-message" rows={5} placeholder="پیام بنویسید..." value={message} onChange={(event) => setMessage(event.target.value)} data-testid="ticket-message" />
          {noMessage ? <FieldError data-testid="ticket-no-message">پیام را بنویسید.</FieldError> : null}
        </Field>
      </Stack>
    </Page>
  )
}

function Bubble({ text, when, mine }: { text: string; when: string; mine: boolean }) {
  return (
    <div className={`flex ${mine ? 'justify-start' : 'justify-end'}`} data-testid={mine ? 'bubble-mine' : 'bubble-theirs'}>
      <Card className={`max-w-[80%] !gap-1 !p-3 ${mine ? ' !bg-primary !text-primary-foreground' : ''}`}>
        <p className="text-sm leading-[22px] break-words whitespace-pre-line">
          <bdi>{text}</bdi>
        </p>
        <p className="text-[11px] opacity-70">{relativeTime(when)}</p>
      </Card>
    </div>
  )
}

// C16.3 and C16.4. A failed send keeps what was typed.
export function TicketScreen() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const client = useQueryClient()
  const ticket = useQuery({ queryKey: ticketKey(id), queryFn: () => unwrap(courierFetch<Thread>(`me/tickets/${id}/`)), retry: false })
  const [text, setText] = useState('')
  const key = useRef(newIdempotencyKey())
  const reply = useMutation({
    mutationFn: () => unwrap(postJson<Thread>(`me/tickets/${id}/messages/`, { message: text }, { key: key.current })),
    onSuccess: (saved) => {
      client.setQueryData(ticketKey(id), saved)
      void client.invalidateQueries({ queryKey: TICKETS_KEY })
      setText('')
      key.current = newIdempotencyKey()
      track('courier.c16.replied')
    },
    onError: (error) => {
      const code = errorCode(error)
      track(`courier.c16.reply_${code}`)
      if (code === 'ticket_closed') return void client.invalidateQueries({ queryKey: ticketKey(id) })
      showToast({ message: 'ارسال نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => reply.mutate() } })
    },
  })
  const close = useMutation({
    mutationFn: () => unwrap(postJson<Thread>(`me/tickets/${id}/close/`)),
    onSuccess: (saved) => {
      client.setQueryData(ticketKey(id), saved)
      void client.invalidateQueries({ queryKey: TICKETS_KEY })
      track('courier.c16.closed')
    },
    onError: () => showToast({ message: 'بسته نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => close.mutate() } }),
  })
  if (ticket.isLoading) {
    return (
      <Page tab={null} back="/me/support" testId="ticket-loading">
        <Stack center>
          <Skeleton className="h-8 w-[60%]" />
          <Skeleton className="h-16 w-full" />
        </Stack>
      </Page>
    )
  }
  if (!ticket.data) {
    return (
      <Page tab={null} back="/me/support">
        {failedScreen(ticket.error, () => void ticket.refetch())}
      </Page>
    )
  }
  const data = ticket.data
  const closed = isClosed(data.status)
  return (
    <Page
      tab={null}
      back="/me/support"
      testId="ticket-thread"
      foot={
        closed ? undefined : (
          <div className="mx-auto flex w-full max-w-[512px] items-center gap-1">
            <Input className="min-w-0 flex-1" placeholder="پیام بنویسید..." value={text} onChange={(event) => setText(event.target.value)} aria-label="پیام" data-testid="reply-text" />
            <Button size="icon" aria-label="ارسال" disabled={!text.trim() || reply.isPending} onClick={() => reply.mutate()} data-testid="reply-send">
              {reply.isPending ? <Spinner /> : <Send />}
            </Button>
          </div>
        )
      }
    >
      <Stack center gap="gap-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-extrabold">{SUBJECTS[data.subject] ?? SUBJECTS.OTHER}</h2>
          <TicketBadge status={data.status} />
        </div>
        {data.messages.map((row) => (
          <Bubble key={row.id} text={row.message} when={row.created_at} mine={row.sender_type === 'USER'} />
        ))}
        {closed ? (
          <>
            <Alert data-testid="ticket-closed">
              <Info />
              <AlertDescription>این تیکت بسته شده است.</AlertDescription>
            </Alert>
            <Button variant="outline" className="w-full" onClick={() => navigate('/me/support/new')} data-testid="ticket-new-after">
              <Plus />
              ایجاد تیکت پشتیبانی
            </Button>
          </>
        ) : (
          <Button variant="ghost" size="sm" className="self-start" disabled={close.isPending} onClick={() => close.mutate()} data-testid="ticket-close">
            بستن تیکت
          </Button>
        )}
      </Stack>
    </Page>
  )
}

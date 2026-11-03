import { useQuery } from '@tanstack/react-query'
import { Bell, CheckCheck, ChevronLeft, CircleCheck, CircleOff, Clock, Route, TimerOff, Users } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { StateBox } from '@/components/state-screen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { fa } from '@/lib/format'
import { courierFetch, unwrap } from './api'
import { query } from './filters'
import { dateLabel, routeLabel, sizeLabel } from './format'
import { H1, Page, Stack } from './page'
import { Segmented } from './parts'
import { failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type Inbox = components['schemas']['Inbox']
type Intro = components['schemas']['Intro']
type Match = components['schemas']['Match']

function Row({ title, meta, tag, onOpen }: { title: string; meta: string; tag: ReactNode; onOpen: () => void }) {
  return (
    <Card className="cursor-pointer !p-4" role="link" tabIndex={0} onClick={onOpen} onKeyDown={(event) => event.key === 'Enter' && onOpen()} data-testid="inbox-row">
      <div className="flex items-center gap-2">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <h3 className="truncate font-bold">
            <bdi>{title}</bdi>
          </h3>
          <p className="truncate text-[13px] text-muted-foreground">{meta}</p>
        </div>
        {tag}
        <ChevronLeft className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </div>
    </Card>
  )
}

function introMeta(intro: Intro) {
  const theirs = intro.role === 'target' ? intro.requester_request : intro.target_request
  const name = intro.role === 'target' ? intro.requester_first_name : null
  return [name, theirs ? dateLabel(theirs) : '', theirs ? sizeLabel(theirs) : ''].filter(Boolean).join(' · ')
}

const matchMetaLine = (match: Match) => `${dateLabel(match.other_request)} · ${sizeLabel(match.my_request)} · ${sizeLabel(match.other_request)}`

function useInbox(state: 'active' | 'archive') {
  return useQuery({ queryKey: ['courier', 'inbox', state], queryFn: () => unwrap(courierFetch<Inbox>(`me/matches/${query({ state })}`)) })
}

// C13: «منتظر پاسخ شما» goes to C19.3, «وصل شدید» to C06, and suggestions are not here: they are under each request in C12.
export function InboxScreen() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<'active' | 'archive'>('active')
  const active = useInbox('active')
  const archive = useInbox('archive')
  const shown = tab === 'active' ? active : archive
  // An accepted intro with its match shows once, as the match.
  const seen = (data?: Inbox) => new Set(data?.matches.map((match) => match.id))
  const accepted = (data?: Inbox) => (data?.accepted ?? []).filter((intro) => !intro.match_id || !seen(data).has(intro.match_id))
  const activeCount = active.data ? active.data.incoming.length + active.data.outgoing.length + accepted(active.data).length + active.data.matches.length : 0
  const archiveCount = archive.data ? archive.data.closed.length + archive.data.matches.length : 0
  const data = shown.data
  const on = (
    <Badge>
      <CircleCheck />
      وصل شدید
    </Badge>
  )
  const rows = data
    ? [
        ...data.incoming.map((intro) => (
          <Row
            key={`in-${intro.id}`}
            title={routeLabel(intro.target_request)}
            meta={introMeta(intro)}
            onOpen={() => navigate(`/me/intros/${intro.id}`)}
            tag={
              <Badge variant="secondary">
                <Bell />
                منتظر پاسخ شما
              </Badge>
            }
          />
        )),
        ...data.outgoing.map((intro) => (
          <Row
            key={`out-${intro.id}`}
            title={routeLabel(intro.target_request)}
            meta={introMeta(intro)}
            onOpen={() => navigate(`/me/intros/${intro.id}`)}
            tag={
              <Badge variant="outline">
                <Clock />
                در انتظار پاسخ
              </Badge>
            }
          />
        )),
        ...accepted(data).map((intro) => <Row key={`on-${intro.id}`} title={routeLabel(intro.target_request)} meta={introMeta(intro)} onOpen={() => navigate(`/me/intros/${intro.id}`)} tag={on} />),
        ...data.closed.map((intro) => (
          <Row
            key={`closed-${intro.id}`}
            title={routeLabel(intro.target_request)}
            meta={introMeta(intro)}
            onOpen={() => navigate(`/me/intros/${intro.id}`)}
            tag={
              intro.status === 'DECLINED' ? (
                <Badge variant="outline">
                  <CircleOff />
                  رد شد
                </Badge>
              ) : (
                <Badge variant="outline">
                  <TimerOff />
                  منقضی
                </Badge>
              )
            }
          />
        )),
        ...data.matches.map((match) => (
          <Row
            key={`match-${match.id}`}
            title={routeLabel(match.other_request)}
            meta={matchMetaLine(match)}
            onOpen={() => navigate(`/me/matches/${match.id}`)}
            tag={
              match.status === 'CONNECTED' ? (
                on
              ) : match.status === 'COMPLETED' ? (
                <Badge variant="secondary">
                  <CheckCheck />
                  تمام‌شده
                </Badge>
              ) : (
                <Badge variant="outline">
                  <TimerOff />
                  منقضی
                </Badge>
              )
            }
          />
        )),
      ]
    : []
  return (
    <Page tab="me" back="/me" testId="inbox">
      <Stack center gap="gap-3">
        <H1>هم‌مسیرها</H1>
        <Segmented
          label="هم‌مسیرها"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'active', label: `فعال (${fa(activeCount)})` },
            { value: 'archive', label: `بایگانی (${fa(archiveCount)})` },
          ]}
        />
        {shown.isLoading ? (
          <div className="flex flex-col gap-3" data-testid="inbox-loading">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : shown.isError ? (
          failedScreen(shown.error, () => void shown.refetch())
        ) : rows.length ? (
          rows
        ) : (
          <StateBox icon={Users} title="هنوز تطبیقی نیست" testId="inbox-empty">
            <Button onClick={() => navigate('/')} data-testid="inbox-search">
              <Route />
              جستجوی مسیر
            </Button>
          </StateBox>
        )}
      </Stack>
    </Page>
  )
}

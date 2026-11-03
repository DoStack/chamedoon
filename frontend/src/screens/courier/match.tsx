import { useQuery } from '@tanstack/react-query'
import { CircleOff, Info, Route, TimerOff } from 'lucide-react'
import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { track } from '@/lib/report'
import { courierFetch, CourierError, errorCode, unwrap } from './api'
import { MatchHead, useContactPanel } from './contact'
import { dateLabel, sizeLabel } from './format'
import { Page, Stack } from './page'
import { OtherWay, routeUrl } from './request'
import { failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type Match = components['schemas']['Match']
type Contact = components['schemas']['Contact']

export const matchKey = (id: string | number) => ['courier', 'match', String(id)]

export const matchRole = (match: Pick<Match, 'role'>) => (match.role === 'supply' ? 'شما مسافر هستید' : 'شما فرستنده هستید')

export function matchMeta(match: Pick<Match, 'my_request' | 'other_request'>) {
  const trip = match.my_request.type === 'SUPPLY' ? match.my_request : match.other_request
  const box = match.my_request.type === 'DEMAND' ? match.my_request : match.other_request
  return `پرواز ${dateLabel(trip)} · ${sizeLabel(trip)} · بسته: ${sizeLabel(box)}`
}

function Connected({ match, contact }: { match: Match; contact: Contact }) {
  const { body, foot } = useContactPanel(contact, match.other_request)
  return (
    <Page tab={null} back="/me/matches" foot={<div className="mx-auto w-full max-w-[512px]">{foot}</div>} testId="match-connected">
      <Stack center>
        <MatchHead card={match.other_request} role={matchRole(match)} meta={matchMeta(match)} />
        {body}
      </Stack>
    </Page>
  )
}

// /me/matches/:id: C06. The server decides what shows: before acceptance it answers with where the intro is.
export function MatchScreen() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const match = useQuery({ queryKey: matchKey(id), queryFn: () => unwrap(courierFetch<Match>(`me/matches/${id}/`)), retry: false })
  const code = match.isError ? errorCode(match.error) : null
  const failure = match.error instanceof CourierError ? match.error.failure : null
  useEffect(() => {
    if (code === 'contact_not_accepted') {
      track('courier.c06.contact_before_accept')
      navigate(failure?.introId ? `/me/intros/${failure.introId}` : '/missing', { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code])

  if (match.isLoading || code === 'contact_not_accepted') {
    return (
      <Page tab={null} back="/me/matches" testId="match-loading">
        <div className="flex h-full items-center justify-center">
          <Spinner />
        </div>
      </Page>
    )
  }
  if (code === 'request_inactive') return <OtherWay icon={CircleOff} title="این سفر بسته شد" route={failure?.route} back="/me/matches" testId="match-taken" />
  if (code === 'match_expired') {
    return (
      <Page
        tab={null}
        back="/me/matches"
        testId="match-expired"
        foot={
          <Button className="w-full" onClick={() => navigate(failure?.route ? routeUrl(failure.route) : '/')} data-testid="same-route">
            <Route />
            مسافرهای همین مسیر
          </Button>
        }
      >
        <Stack center>
          <div className="flex items-center gap-2 font-bold">
            <TimerOff className="size-5" aria-hidden />
            منقضی
          </div>
          <Alert>
            <Info />
            <AlertDescription>این هماهنگی منقضی شد و راه تماس دیگر نشان داده نمی‌شود.</AlertDescription>
          </Alert>
        </Stack>
      </Page>
    )
  }
  if (!match.data) {
    return (
      <Page tab={null} back="/me/matches">
        {failedScreen(match.error, () => void match.refetch())}
      </Page>
    )
  }
  if (!match.data.contact) {
    return <OtherWay icon={CircleOff} title="این سفر بسته شد" back="/me/matches" testId="match-taken" />
  }
  return <Connected match={match.data} contact={match.data.contact} />
}

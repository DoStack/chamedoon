import { useQuery } from '@tanstack/react-query'
import { Bell, CircleAlert, CircleCheck, Info, Megaphone, RefreshCw, Route, Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { AlertSheet } from './alert-sheet'
import { courierFetch, unwrap } from './api'
import { query } from './filters'
import { nounFor, placeKey, type Side } from './format'
import { H1, Page, Stack } from './page'
import { CardGrid, RequestCardView, RequestSkeleton, Section } from './parts'
import { useMine } from './post'
import { failedScreen } from './states'
import type { components } from '@/api/courier-schema.gen'

type Counterparts = components['schemas']['Counterparts']

export const counterpartsKey = (id: string | number) => ['courier', 'counterparts', String(id)]

export function useCounterparts(id: string, enabled = true) {
  return useQuery({ queryKey: counterpartsKey(id), queryFn: () => unwrap(courierFetch<Counterparts>(`me/requests/${id}/counterparts/`)), enabled, retry: false })
}

function Head({ children }: { children?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <CircleCheck className="size-6 shrink-0 text-primary" aria-hidden />
        <H1>درخواست شما ثبت شد</H1>
      </div>
      {children}
    </div>
  )
}

// C09: right after posting. Suggestions only: nobody is connected and nobody is told.
export function PostedScreen() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const mine = useMine(id)
  const counterparts = useCounterparts(id, mine.isSuccess)
  const [alert, setAlert] = useState(false)
  const item = mine.data
  const rows = counterparts.data?.items ?? []

  useEffect(() => {
    if (counterparts.isSuccess) track(rows.length ? 'courier.c09.suggested' : 'courier.c09.nobody')
    if (counterparts.isError) track('courier.c09.matches_failed')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [counterparts.isSuccess, counterparts.isError])

  if (mine.isLoading) {
    return (
      <Page tab="post" back="/post" testId="posted-loading">
        <Stack>
          <Skeleton className="h-8 w-[60%]" />
          <Skeleton className="h-16 w-full" />
        </Stack>
      </Page>
    )
  }
  if (!item) {
    return (
      <Page tab="post" back="/post">
        {failedScreen(mine.error, () => void mine.refetch())}
      </Page>
    )
  }

  const side: Side = item.type === 'SUPPLY' ? 'SUPPLY' : 'DEMAND'
  const noun = nounFor(side)
  const from = placeKey(item.origin)
  const to = item.stops[0] ? placeKey(item.stops[0]) : ''
  const browse = `/${query({ side, from, to })}`
  const actions = (
    <div className="flex gap-2">
      <Button className="flex-1" onClick={() => navigate(browse)} data-testid="posted-browse">
        <Search />
        مرور درخواست‌ها
      </Button>
      <Button variant="outline" className="flex-1" onClick={() => navigate('/me/requests')} data-testid="posted-mine">
        درخواست‌های من
      </Button>
    </div>
  )
  const channel = item.channel_on ? (
    <Alert data-testid="posted-channel">
      <Megaphone />
      <AlertDescription>درخواست شما بدون نام و آیدی در کانال منتشر می‌شود.</AlertDescription>
    </Alert>
  ) : (
    <Alert data-testid="posted-channel-off">
      <Info />
      <AlertDescription>انتشار در کانال فعلاً خاموش است. درخواست شما در ربات و مینی‌اپ چمدون دیده می‌شود.</AlertDescription>
    </Alert>
  )
  const alertSheet = <AlertSheet open={alert} onClose={() => setAlert(false)} draft={{ side, from, to, month: '', size: '' }} />

  if (counterparts.isSuccess && !rows.length) {
    return (
      <Page tab="post" back="/post" testId="posted-nobody">
        <div className="flex h-full flex-col items-center justify-center gap-2 p-4">
          <Head />
          <StateBox icon={Route} title={`هنوز ${noun === 'مسافر' ? 'مسافری' : 'بسته‌ای'} برای این مسیر نیست`}>
            <div className="flex w-full flex-col gap-2">
              <Button className="w-full" onClick={() => setAlert(true)} data-testid="posted-alert">
                <Bell />
                گوش به زنگ
              </Button>
              <Button variant="outline" className="w-full" onClick={() => navigate('/me/requests')} data-testid="posted-mine">
                درخواست‌های من
              </Button>
            </div>
          </StateBox>
        </div>
        {alertSheet}
      </Page>
    )
  }
  return (
    <Page tab="post" back="/post" testId="posted">
      <Stack>
        <Head>
          {counterparts.isError ? (
            <Alert data-testid="posted-failed">
              <CircleAlert />
              <AlertDescription className="flex w-full items-center justify-between gap-2">
                {noun === 'مسافر' ? 'فهرست مسافرها' : 'فهرست بسته‌ها'} بارگذاری نشد
                <Button variant="outline" size="sm" onClick={() => void counterparts.refetch()}>
                  <RefreshCw />
                  تلاش مجدد
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            channel
          )}
        </Head>
        {counterparts.isLoading ? (
          <CardGrid>
            <RequestSkeleton />
            <RequestSkeleton />
          </CardGrid>
        ) : rows.length ? (
          <Section title={item.channel_on ? 'تطبیق‌های پیشنهادی' : 'درخواست‌های متناسب'} side={`${fa(rows.length)} ${noun}`}>
            <CardGrid>
              {rows.map((row) => (
                <RequestCardView key={row.id} card={row} onOpen={() => navigate(`/request/${row.id}`)} />
              ))}
            </CardGrid>
          </Section>
        ) : null}
        {actions}
      </Stack>
    </Page>
  )
}

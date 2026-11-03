import { useInfiniteQuery } from '@tanstack/react-query'
import { Bookmark, BookmarkX, CircleAlert, Pencil, RefreshCw, Search, SearchX, StickyNote } from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useNavigationType } from 'react-router-dom'
import type { SavedList, SavedRow } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { CardSkeleton, ListingCard } from '@/components/listing-card'
import { goToList, MainNav } from '@/components/main-nav'
import { StateBox } from '@/components/state-screen'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { ApiFailureError, meFetch, unwrap } from '@/lib/api'
import { cardTitle, currency, fa } from '@/lib/format'
import { track } from '@/lib/report'
import { isRemoved, NOTE_MAX, rememberRows, SAVED_KEY, saveNote, unmark, useRemovedVersion } from '@/lib/saved'
import { OfflineBar, SlowNote, useAfter, useOnline } from './global-states'

let leftTop = 0

// F14#3, #9. The body starts from the saved note, and a failure keeps what was typed.
function NoteSheet({ row, onClose }: { row: SavedRow; onClose: () => void }) {
  const [text, setText] = useState(row.note)
  const [sending, setSending] = useState(false)
  const card = row.card
  const save = async () => {
    setSending(true)
    const done = await saveNote(row.listing_id, row.note, text)
    setSending(false)
    if (done) onClose()
  }
  return (
    <AppSheet
      open
      onClose={onClose}
      label="یادداشت"
      title="یادداشت"
      testId="note-sheet"
      foot={
        <Button className="w-full" disabled={sending} onClick={() => void save()}>
          {sending ? <Spinner /> : null}
          ذخیره یادداشت
        </Button>
      }
    >
      {card ? (
        <p className="m-0 text-sm text-muted-foreground">
          {card.price_amount != null ? (
            <>
              {fa(card.price_amount)} <bdi dir="ltr">{currency(card.price_currency)}</bdi> ·{' '}
            </>
          ) : null}
          {cardTitle(card)}
        </p>
      ) : null}
      <Field>
        <FieldLabel htmlFor="saved-note">یادداشت شما</FieldLabel>
        <Textarea id="saved-note" dir="auto" rows={4} maxLength={NOTE_MAX} value={text} onChange={(event) => setText(event.target.value)} />
        <FieldDescription data-testid="note-count">
          {fa(text.length)} / {fa(NOTE_MAX)}
        </FieldDescription>
      </Field>
    </AppSheet>
  )
}

function NoteLine({ row, onEdit }: { row: SavedRow; onEdit: () => void }) {
  if (!row.note) {
    return (
      <Button variant="ghost" size="sm" className="self-start" data-testid="note-add" onClick={onEdit}>
        <StickyNote />
        افزودن یادداشت
      </Button>
    )
  }
  return (
    <div className="flex items-center gap-1.5" data-testid="note-line">
      <StickyNote className="size-4 flex-none" aria-hidden />
      <span dir="auto" className="min-w-0 flex-1 text-[13px] break-words">
        یادداشت: {row.note}
      </span>
      <Button variant="ghost" size="sm" onClick={onEdit}>
        <Pencil />
        ویرایش
      </Button>
    </div>
  )
}

function Row({ row, onEdit }: { row: SavedRow; onEdit: () => void }) {
  const navigate = useNavigate()
  const card = row.card
  // F14#8
  if (row.state === 'gone' || !card) {
    return (
      <div className="grid min-w-0 flex-none" data-testid="saved-gone">
        <Card className="h-full gap-3 p-4">
          <div className="flex items-center gap-2">
            <SearchX className="size-5 flex-none" aria-hidden />
            <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">این آگهی دیگر نیست</h3>
          </div>
          <Button variant="outline" className="w-full" onClick={() => unmark(row.listing_id, false, false)}>
            <BookmarkX />
            برداشتن نشان
          </Button>
        </Card>
      </div>
    )
  }
  const closed = row.state === 'closed'
  return (
    <ListingCard
      card={card}
      closed={closed}
      action={
        <div className="flex flex-col gap-2">
          <NoteLine row={row} onEdit={onEdit} />
          {closed ? (
            <Button variant="outline" className="w-full" data-testid="saved-similar" onClick={() => navigate(`/?city=${card.metro_slug}&types=${card.property_type}`)}>
              آگهی‌های مشابه
            </Button>
          ) : null}
        </div>
      }
    />
  )
}

const OFFLINE = new Set(['offline', 'network_error', 'timeout'])

// F14. Every state keeps the tab, as the flow draws it.
export function SavedScreen() {
  const online = useOnline()
  useRemovedVersion()
  const navigationType = useNavigationType()
  const [editing, setEditing] = useState<SavedRow | null>(null)
  const query = useInfiniteQuery({
    queryKey: SAVED_KEY,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const page = await unwrap(meFetch<SavedList>(`/api/v1/me/saved/${pageParam ? `?cursor=${pageParam}` : ''}`))
      rememberRows(page.results)
      return page
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 0,
  })
  const fetched = query.data?.pages.flatMap((page) => page.results) ?? []
  const rows = fetched.filter((row) => !isRemoved(row))
  const total = (query.data?.pages[0]?.total ?? 0) - (fetched.length - rows.length)
  const code = query.error instanceof ApiFailureError ? query.error.failure.code : null
  const failed = !query.data && query.isError
  const empty = Boolean(query.data) && rows.length === 0 && !query.hasNextPage
  const slow = useAfter(3000, query.isPending)
  const scroller = useRef<HTMLDivElement>(null)
  const sentinel = useRef<HTMLDivElement>(null)
  const top = useRef(0)

  useEffect(() => {
    if (query.data) track(empty ? 'saved_empty' : 'saved_view', { total })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [Boolean(query.data), empty])
  useEffect(() => {
    if (failed) track(code && OFFLINE.has(code) ? 'saved_offline' : 'saved_failed', { code })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failed])
  const closedCount = rows.filter((row) => row.state === 'closed').length
  const goneCount = rows.filter((row) => row.state === 'gone').length
  useEffect(() => {
    if (closedCount) track('saved_closed', { count: closedCount })
    if (goneCount) track('saved_gone', { count: goneCount })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [closedCount > 0, goneCount > 0])

  // Back from a listing returns to the same place, as on the list.
  const restored = useRef(navigationType !== 'POP')
  useLayoutEffect(() => {
    if (restored.current || !scroller.current) return
    restored.current = true
    scroller.current.scrollTop = leftTop
  })
  useEffect(() => () => void (leftTop = top.current), [])

  useEffect(() => {
    const element = sentinel.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && query.hasNextPage && !query.isFetchingNextPage && !query.isFetchNextPageError) {
          track('saved_more')
          void query.fetchNextPage()
        }
      },
      { root: scroller.current, rootMargin: '400px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.hasNextPage, query.isFetchingNextPage, query.isFetchNextPageError, rows.length])

  const grid = 'grid auto-rows-max grid-cols-1 gap-4 pt-1 md:grid-cols-3'
  let body: React.ReactNode
  if (query.isPending) {
    body = (
      <>
        {slow ? <SlowNote /> : null}
        <div className={`${grid} min-h-0 flex-1 overflow-hidden`} data-testid="saved-loading">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      </>
    )
  } else if (failed) {
    // F25#8
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={CircleAlert} title="بارگذاری نشد" testId="saved-failed">
          <Button className="w-full" onClick={() => void query.refetch()}>
            <RefreshCw />
            تلاش مجدد
          </Button>
        </StateBox>
      </div>
    )
  } else if (empty) {
    // F14#6
    body = (
      <div className="flex min-h-0 flex-1 items-center justify-center">
        <StateBox icon={Bookmark} title="هنوز آگهی‌ای نشان نکرده‌اید" testId="saved-empty">
          <Button onClick={() => goToList()}>
            <Search />
            دیدن آگهی‌ها
          </Button>
        </StateBox>
      </div>
    )
  } else {
    const remember = (event: React.SyntheticEvent<HTMLDivElement>) => (top.current = event.currentTarget.scrollTop)
    body = (
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto pb-4" data-testid="saved-scroll" onScroll={remember} onClickCapture={remember}>
        <div className={grid}>
          {rows.map((row) => (
            <Row key={row.listing_id} row={row} onEdit={() => setEditing(row)} />
          ))}
          {query.isFetchingNextPage ? <CardSkeleton /> : null}
          {query.isFetchNextPageError ? (
            <Alert className="col-span-full" data-testid="more-failed">
              <CircleAlert />
              <AlertDescription className="flex w-full items-center justify-between gap-2">
                <span className="text-foreground">ادامه‌ی آگهی‌ها بارگذاری نشد</span>
                <Button variant="outline" size="sm" onClick={() => void query.fetchNextPage()}>
                  <RefreshCw />
                  تلاش مجدد
                </Button>
              </AlertDescription>
            </Alert>
          ) : null}
        </div>
        <div ref={sentinel} className="h-px" />
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col" data-testid="saved-screen">
      <MainNav place="top" active="saved" />
      <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-3 md:px-6">
        {online ? null : <OfflineBar onRetry={() => void query.refetch()} />}
        <div className="flex flex-none items-baseline justify-between gap-2">
          <h1 className="m-0 text-2xl leading-8 font-extrabold">نشان‌شده‌ها</h1>
          {query.data && !empty ? (
            <span className="text-sm font-semibold text-muted-foreground" data-testid="saved-count">
              آگهی‌ها ({fa(total)})
            </span>
          ) : null}
        </div>
        {body}
      </div>
      <MainNav place="bottom" active="saved" />
      {editing ? <NoteSheet key={editing.listing_id} row={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  )
}

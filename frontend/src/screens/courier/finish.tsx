import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, CheckCheck, CircleAlert, Info, Send, Star, TimerOff } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, FieldDescription, FieldError, FieldLabel } from '@/components/ui/field'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { fa } from '@/lib/format'
import { track } from '@/lib/report'
import { getWebApp, isVersionAtLeast } from '@/lib/telegram'
import { CourierError, errorCode, postJson, unwrap } from './api'
import { MatchHead } from './contact'
import { matchKey, matchMeta, matchRole } from './match'
import { ME_KEY, Page, Stack } from './page'
import { copyWithToast } from './states'
import type { components } from '@/api/courier-schema.gen'

type Match = components['schemas']['Match']

const COMMENT_MAX = 500

export function Stars({ value, onPick, testId }: { value: number; onPick?: (score: number) => void; testId?: string }) {
  return (
    <div className="flex items-center" data-testid={testId} role={onPick ? 'radiogroup' : 'img'} aria-label={`${fa(value)} از ۵ ستاره`}>
      {[1, 2, 3, 4, 5].map((score) =>
        onPick ? (
          <Button key={score} variant="ghost" size="icon" aria-label={`${fa(score)} ستاره`} aria-pressed={score <= value} className={score <= value ? '!text-primary [&_svg]:fill-current' : ''} onClick={() => onPick(score)} data-testid={`star-${score}`}>
            <Star />
          </Button>
        ) : (
          <Star key={score} className={`size-5 ${score <= value ? 'fill-current text-primary' : 'text-muted-foreground'}`} aria-hidden />
        ),
      )}
    </div>
  )
}

// C14.1: finishing is one-sided, as in the code, and closes both requests.
// An accepted intro finishes the same way (C19.4), through its own path, since it may have no match until then.
export function FinishButton({ path, matchId, onNotOpen, onDone }: { path: string; matchId: number | null; onNotOpen: () => void; onDone?: (saved: Match) => void }) {
  const client = useQueryClient()
  const [asking, setAsking] = useState(false)
  const finish = useMutation({
    mutationFn: () => unwrap(postJson<Match>(path)),
    onSuccess: (saved) => {
      client.setQueryData(matchKey(saved.id), saved)
      void client.invalidateQueries({ queryKey: ME_KEY })
      void client.invalidateQueries({ queryKey: ['courier', 'inbox'] })
      track('courier.c14.completed')
      track('courier_match_finish', { match_id: saved.id })
      onDone?.(saved)
    },
    onError: (error) => {
      track('courier_match_finish_failed', { match_id: matchId, code: errorCode(error) })
      if (errorCode(error) === 'match_not_open') {
        track('courier.c14.complete_rejected')
        return onNotOpen()
      }
      showToast({ message: 'ثبت نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'match_finish' }), finish.mutate()) } })
    },
  })
  return (
    <>
      <Button
        variant="outline"
        className="w-full"
        disabled={finish.isPending}
        onClick={() => {
          track('courier_match_finish_tap', { match_id: matchId })
          setAsking(true)
        }}
        data-testid="finish"
      >
        {finish.isPending ? <Spinner /> : <Check />}
        اتمام سفارش
      </Button>
      <AlertDialog open={asking} onOpenChange={setAsking}>
        <AlertDialogContent dir="rtl" data-testid="finish-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>سفارش تمام شود؟</AlertDialogTitle>
            <AlertDialogDescription>بعد از آن هر دو می‌توانید امتیاز بدهید.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => track('courier_match_finish_cancel', { match_id: matchId })}>نه</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                track('courier_match_finish_confirm', { match_id: matchId })
                finish.mutate()
              }}
              data-testid="finish-go"
            >
              بله، تمام شد
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

export function NotOpenScreen({ matchId, card, role }: { matchId: number | null; card: Match['other_request']; role: string }) {
  const navigate = useNavigate()
  useEffect(() => track('courier_match_not_open_shown', { match_id: matchId }), [matchId])
  return (
    <Page
      tab={null}
      back="/me/matches"
      testId="match-not-open"
      foot={
        <Button
          variant="outline"
          className="w-full"
          onClick={() => {
            track('courier_match_not_open_to_inbox', { match_id: matchId })
            navigate('/me/matches')
          }}
          data-testid="to-inbox"
        >
          هم‌مسیرها
        </Button>
      }
    >
      <Stack center>
        <MatchHead status="منقضی" icon={TimerOff} card={card} role={role} />
        <Alert variant="destructive">
          <CircleAlert />
          <AlertDescription>فقط هماهنگی باز را می‌شود تمام کرد.</AlertDescription>
        </Alert>
      </Stack>
    </Page>
  )
}

function Thanks({ match }: { match: Match }) {
  const contact = match.contact
  if (!contact) return null
  const send = async () => {
    const app = getWebApp()
    if (contact.url && app?.openTelegramLink && isVersionAtLeast('6.1')) {
      track('courier_match_thanks_tap', { match_id: match.id, opened: true })
      app.openTelegramLink(contact.url)
      return
    }
    track('courier.c14.link_failed')
    track('courier_match_thanks_tap', { match_id: match.id, opened: false })
    if (contact.telegram_username) await copyWithToast(`@${contact.telegram_username}`, 'تلگرام باز نشد. نام کاربری کپی شد.', 'تلگرام باز نشد')
    else showToast({ message: 'تلگرام باز نشد', icon: 'alert' })
  }
  return (
    <Button variant="outline" className="ph-no-capture w-full" onClick={() => void send()} data-testid="thanks">
      <Send />
      قدردانی با پیام
    </Button>
  )
}

// C14.2 to C14.6: the rate form, then the scores. Ratings of 3 and above go to the channel with the comment; the form says so.
export function FinishedScreen({ match }: { match: Match }) {
  const client = useQueryClient()
  const [score, setScore] = useState(0)
  const [comment, setComment] = useState('')
  const [noScore, setNoScore] = useState(false)
  const [already, setAlready] = useState(false)
  const rating = match.rating
  const tooLong = comment.length > COMMENT_MAX
  useEffect(() => {
    track(rating.can_rate && !already ? 'courier_match_rate_form_shown' : 'courier_match_rated_view', { match_id: match.id, my_stars: rating.my_score ?? null, their_stars: rating.their_score ?? null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, rating.can_rate, already])
  useEffect(() => {
    if (rating.their_score) track('courier_match_other_side', { match_id: match.id, their_stars: rating.their_score })
  }, [match.id, rating.their_score])
  const rate = useMutation({
    mutationFn: () => unwrap(postJson<Match>(`me/matches/${match.id}/rate/`, { score, comment })),
    onSuccess: (saved) => {
      client.setQueryData(matchKey(match.id), saved)
      showToast({ message: 'امتیاز شما ثبت شد', icon: 'check' })
      track(saved.rating.their_score ? 'courier.c14.rate_both' : 'courier.c14.rate_waiting')
      track('courier_match_rating_sent', { match_id: match.id, stars: score, comment_length: comment.length, other_side_rated: Boolean(saved.rating.their_score) })
    },
    onError: (error) => {
      const code = errorCode(error)
      track('courier_match_rating_failed', { match_id: match.id, code })
      if (code === 'already_rated') {
        setAlready(true)
        track('courier.c14.rate_double')
        return void client.invalidateQueries({ queryKey: matchKey(match.id) })
      }
      if (code === 'rating_invalid') return setNoScore(!score || Boolean((error as CourierError).failure.fields?.score))
      showToast({ message: 'ثبت نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => (track('courier_retry_tap', { where: 'match_rate' }), rate.mutate()) } })
    },
  })
  const head = <MatchHead status="تمام شد" icon={CheckCheck} card={match.other_request} role={matchRole(match)} meta={matchMeta(match)} />
  if (rating.can_rate && !already) {
    return (
      <Page
        tab={null}
        back="/me/matches"
        testId="rate-form"
        foot={
          <div className="mx-auto w-full max-w-[512px]">
            <Button
              className="w-full"
              disabled={rate.isPending}
              onClick={() => {
                if (!score) {
                  track('courier_match_rate_validation_failed', { match_id: match.id, field: 'stars', code: 'required' })
                  return setNoScore(true)
                }
                if (tooLong) track('courier_match_rate_validation_failed', { match_id: match.id, field: 'comment', code: 'too_long', comment_length: comment.length })
                if (!tooLong) rate.mutate()
              }}
              data-testid="rate-send"
            >
              {rate.isPending ? <Spinner /> : null}
              ثبت امتیاز
            </Button>
          </div>
        }
      >
        <Stack center>
          {head}
          <Card className="!gap-3 !p-4">
            <h3 className="font-bold">امتیاز به این سفارش</h3>
            <Stars
              value={score}
              testId="rate-stars"
              onPick={(next) => {
                track('courier_match_star_pick', { match_id: match.id, stars: next })
                setScore(next)
                setNoScore(false)
              }}
            />
            {noScore ? <FieldError data-testid="rate-no-score">امتیازی از ۱ تا ۵ انتخاب کنید.</FieldError> : null}
            <Field data-invalid={tooLong || undefined}>
              <FieldLabel htmlFor="rate-comment">نظر</FieldLabel>
              <Textarea className="ph-no-capture" id="rate-comment" rows={3} placeholder="تحویل چطور بود؟ (اختیاری)" value={comment} onChange={(event) => setComment(event.target.value)} data-testid="rate-comment" />
              <FieldDescription data-testid="rate-disclosure">امتیاز ۳ به بالا با نظر شما در کانال منتشر می‌شود.</FieldDescription>
              {tooLong ? <FieldError data-testid="rate-too-long">نظر باید کمتر از ۵۰۰ نویسه باشد.</FieldError> : null}
            </Field>
          </Card>
        </Stack>
      </Page>
    )
  }
  return (
    <Page tab={null} back="/me/matches" testId="rated" foot={<div className="mx-auto w-full max-w-[512px]">{<Thanks match={match} />}</div>}>
      <Stack center>
        {head}
        {already ? (
          <Alert data-testid="already-rated">
            <Info />
            <AlertDescription>قبلاً برای این سفارش امتیاز داده‌اید.</AlertDescription>
          </Alert>
        ) : null}
        <Card className="!gap-3 !p-4">
          {rating.my_score ? (
            <div className="flex items-center justify-between gap-2" data-testid="my-score">
              <span className="text-sm">امتیاز شما</span>
              <Stars value={rating.my_score} />
            </div>
          ) : null}
          {rating.their_score ? (
            <div className="flex items-center justify-between gap-2" data-testid="their-score">
              <span className="text-sm">امتیاز طرف مقابل</span>
              <Stars value={rating.their_score} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="rate-waiting">
              منتظر امتیاز طرف مقابل.
            </p>
          )}
        </Card>
      </Stack>
    </Page>
  )
}

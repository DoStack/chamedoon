import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, CheckCheck, CircleAlert, Info, Send, Star, TimerOff } from 'lucide-react'
import { useState } from 'react'
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
export function FinishButton({ match, onNotOpen }: { match: Match; onNotOpen: () => void }) {
  const client = useQueryClient()
  const [asking, setAsking] = useState(false)
  const finish = useMutation({
    mutationFn: () => unwrap(postJson<Match>(`me/matches/${match.id}/complete/`)),
    onSuccess: (saved) => {
      client.setQueryData(matchKey(match.id), saved)
      void client.invalidateQueries({ queryKey: ME_KEY })
      void client.invalidateQueries({ queryKey: ['courier', 'inbox'] })
      track('courier.c14.completed')
    },
    onError: (error) => {
      if (errorCode(error) === 'match_not_open') {
        track('courier.c14.complete_rejected')
        return onNotOpen()
      }
      showToast({ message: 'ثبت نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => finish.mutate() } })
    },
  })
  return (
    <>
      <Button variant="outline" className="w-full" disabled={finish.isPending} onClick={() => setAsking(true)} data-testid="finish">
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
            <AlertDialogCancel>نه</AlertDialogCancel>
            <AlertDialogAction onClick={() => finish.mutate()} data-testid="finish-go">
              بله، تمام شد
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

export function NotOpenScreen({ match }: { match: Match }) {
  const navigate = useNavigate()
  return (
    <Page
      tab={null}
      back="/me/matches"
      testId="match-not-open"
      foot={
        <Button variant="outline" className="w-full" onClick={() => navigate('/me/matches')} data-testid="to-inbox">
          هم‌مسیرها
        </Button>
      }
    >
      <Stack center>
        <MatchHead status="منقضی" icon={TimerOff} card={match.other_request} role={matchRole(match)} />
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
      app.openTelegramLink(contact.url)
      return
    }
    track('courier.c14.link_failed')
    if (contact.telegram_username) await copyWithToast(`@${contact.telegram_username}`, 'تلگرام باز نشد. نام کاربری کپی شد.', 'تلگرام باز نشد')
    else showToast({ message: 'تلگرام باز نشد', icon: 'alert' })
  }
  return (
    <Button variant="outline" className="w-full" onClick={() => void send()} data-testid="thanks">
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
  const rate = useMutation({
    mutationFn: () => unwrap(postJson<Match>(`me/matches/${match.id}/rate/`, { score, comment })),
    onSuccess: (saved) => {
      client.setQueryData(matchKey(match.id), saved)
      showToast({ message: 'امتیاز شما ثبت شد', icon: 'check' })
      track(saved.rating.their_score ? 'courier.c14.rate_both' : 'courier.c14.rate_waiting')
    },
    onError: (error) => {
      const code = errorCode(error)
      if (code === 'already_rated') {
        setAlready(true)
        track('courier.c14.rate_double')
        return void client.invalidateQueries({ queryKey: matchKey(match.id) })
      }
      if (code === 'rating_invalid') return setNoScore(!score || Boolean((error as CourierError).failure.fields?.score))
      showToast({ message: 'ثبت نشد', icon: 'alert', action: { label: 'تلاش مجدد', onClick: () => rate.mutate() } })
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
                if (!score) return setNoScore(true)
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
                setScore(next)
                setNoScore(false)
              }}
            />
            {noScore ? <FieldError data-testid="rate-no-score">امتیازی از ۱ تا ۵ انتخاب کنید.</FieldError> : null}
            <Field data-invalid={tooLong || undefined}>
              <FieldLabel htmlFor="rate-comment">نظر</FieldLabel>
              <Textarea id="rate-comment" rows={3} placeholder="تحویل چطور بود؟ (اختیاری)" value={comment} onChange={(event) => setComment(event.target.value)} data-testid="rate-comment" />
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

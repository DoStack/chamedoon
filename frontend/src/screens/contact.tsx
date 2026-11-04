import { Ban, Check, CircleAlert, CircleCheck, CircleOff, Copy, ExternalLink, Hourglass, LifeBuoy, Megaphone, Phone, RefreshCw, Send, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Contact, GateChannel, Listing } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { BookmarkAction } from '@/components/bookmark'
import { needsInternetToast, showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { type ApiFailure, meFetch } from '@/lib/api'
import { REGION_RULES } from '@/lib/format'
import { openExternal, SUPPORT_URL } from '@/lib/links'
import { overrideCard } from '@/lib/overrides'
import { track } from '@/lib/report'
import { getWebApp } from '@/lib/telegram'

type Entry = 'contact' | 'source'

type Step =
  | { name: 'idle' }
  | { name: 'warning'; entry: Entry } // F11#1, #21
  | { name: 'gate'; entry: Entry; channel: GateChannel | null; joinUrl: string; stillOut: boolean; checking: boolean } // F11#2, #3, #5, #11, #16
  | { name: 'join'; entry: Entry; channel: GateChannel | null; joinUrl: string } // F11#4
  | { name: 'revealed'; contact: Contact; short: boolean } // F11#6, #7, #18, #19, #20, #22
  | { name: 'post_only'; contact: Contact } // F11#8
  | { name: 'nothing' } // F11#17
  | { name: 'closed' } // F11#10
  | { name: 'gone' } // F02#4
  | { name: 'cap' } // F11#12
  | { name: 'failed'; entry: Entry } // F11#13
  | { name: 'rate'; entry: Entry } // F11#14
  | { name: 'banned' } // F11#15

const WARNED = 'chamedoon.warned'
const WARNING = 'تا خانه را ندیده‌اید و قرارداد نبسته‌اید، پولی نفرستید. کسی که «فقط از خارج» است یا کارت هدیه می‌خواهد، نشانه‌ی کلاهبرداری است.'

function hasBeenWarned() {
  try {
    return localStorage.getItem(WARNED) === '1'
  } catch {
    return false
  }
}

function markWarned() {
  try {
    localStorage.setItem(WARNED, '1')
  } catch {
    // Private mode: the full warning comes again next time.
  }
}

// Everything between «تماس» and the contact: F11. Screens switch on `code`. The server upserts, so a retry is safe.
export function useContactFlow(listing: Listing | null) {
  const [step, setStep] = useState<Step>({ name: 'idle' })
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  const id = listing?.id

  const reveal = useCallback(
    async (entry: Entry, opts: { fromGate?: 'tap' | 'focus'; short?: boolean } = {}) => {
      if (inFlight.current || !id) return
      inFlight.current = true
      setPending(true)
      if (opts.fromGate) setStep((current) => (current.name === 'gate' ? { ...current, checking: opts.fromGate === 'tap' } : current))
      const result = await meFetch<Contact>(`/api/v1/me/listings/${id}/contact/`, { method: 'POST' })
      inFlight.current = false
      setPending(false)
      if (result.ok) {
        const contact = result.data
        if (!contact.has_any) return setStep({ name: 'nothing' })
        track(contact.gate === 'failed_open' ? 'gate_fail_open' : 'contact_revealed', { gate: contact.gate })
        // has_any covers a source post alone, so a listing with only that reaches F11#8 and its button works.
        if (entry === 'source' && contact.source_url) openExternal(contact.source_url)
        if (!contact.telegram && !contact.phone) return setStep({ name: 'post_only', contact })
        return setStep({ name: 'revealed', contact, short: opts.short ?? hasBeenWarned() })
      }
      onFailure(result, entry, opts.fromGate)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id],
  )

  const onFailure = (failure: ApiFailure, entry: Entry, fromGate?: 'tap' | 'focus') => {
    switch (failure.code) {
      case 'not_member':
        track(fromGate ? 'gate_still_out' : 'gate_shown')
        return setStep({ name: 'gate', entry, channel: failure.channel ?? listing?.channel ?? null, joinUrl: failure.joinUrl ?? '', stillOut: fromGate === 'tap', checking: false })
      case 'listing_closed':
        track('gate_listing_closed')
        return setStep({ name: 'closed' })
      case 'listing_gone':
        return setStep({ name: 'gone' })
      case 'no_channel':
        // The gate is on and no channel can be asked for, so the contact stays closed for good: retry would never help.
        track('gate_no_channel')
        return setStep({ name: 'nothing' })
      case 'reveal_cap':
        track('reveal_capped')
        return setStep({ name: 'cap' })
      case 'gate_banned':
        track('gate_banned')
        return setStep({ name: 'banned' })
      case 'rate_limited':
        track('rate_limited')
        return setStep({ name: 'rate', entry })
      // These replace the whole app through onApiFailure, so the sheet just closes.
      case 'maintenance':
      case 'session_expired':
      case 'auth_invalid':
      case 'auth_outside':
      case 'user_blocked':
      case 'miniapp_off':
        return setStep({ name: 'idle' })
      default:
        // offline, timeout, 5xx, a platform page, not_served_yet until backend PR 3.
        track('gate_failed', { code: failure.code })
        return setStep({ name: 'failed', entry })
    }
  }

  const start = useCallback(
    (entry: Entry) => {
      if (!listing) return
      // F25#7
      if (navigator.onLine === false) return needsInternetToast()
      track(entry === 'source' ? 'source_tap' : 'contact_tap')
      if (!listing.has_telegram && !listing.has_phone && !listing.has_source) return setStep({ name: 'nothing' })
      if (!hasBeenWarned()) {
        track('scam_warning_seen')
        return setStep({ name: 'warning', entry })
      }
      void reveal(entry)
    },
    [listing, reveal],
  )

  // F11#10 ends in the closed detail, not back at an active page that offers «تماس» again.
  const finishClosed = useCallback(() => {
    if (listing) overrideCard({ ...listing, closed: true })
    setStep({ name: 'idle' })
  }, [listing])

  const close = useCallback(() => setStep({ name: 'idle' }), [])

  return { step, setStep, pending, start, reveal, close, finishClosed }
}

function Masked({ listing }: { listing: Listing }) {
  // What the listing offers, with no digit or letter of it (P1-35).
  if (!listing.has_telegram && !listing.has_phone) return null
  return (
    <Card className="gap-3 p-4" data-testid="masked">
      {listing.has_telegram ? (
        <div className="flex items-center gap-2 text-sm">
          <Send className="size-4" aria-hidden />
          تلگرام
        </div>
      ) : null}
      {listing.has_phone ? (
        <div className="flex items-center gap-2 text-sm">
          <Phone className="size-4" aria-hidden />
          شماره
        </div>
      ) : null}
    </Card>
  )
}

function channelTitle(channel: GateChannel | null) {
  return channel?.title || 'کانال چمدون - خانه'
}

function gateTitle(channel: GateChannel | null, listing: Listing) {
  // The sheet names the listing's own channel, or the country's when the city has none (F11#16).
  const name = channel?.title.split(/ [·-] /).pop()
  if (name) return `عضو کانال ${name} شوید`
  return listing.country === 'DE' ? 'عضو کانال آلمان شوید' : 'عضو کانال کانادا شوید'
}

function ProblemBody({ icon: Icon, message }: { icon: typeof Ban; message: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-4 text-center">
      <span className="flex size-10 items-center justify-center rounded-lg bg-muted">
        <Icon className="size-6" aria-hidden />
      </span>
      <p className="m-0 text-sm font-medium">{message}</p>
    </div>
  )
}

function Stack({ children }: { children: ReactNode }) {
  return <div className="flex flex-col gap-2">{children}</div>
}

function isCopyFailureHandled(text: string) {
  return navigator.clipboard?.writeText(text).then(
    () => true,
    () => false,
  ) ?? Promise.resolve(false)
}

function Revealed({ contact, short }: { contact: Contact; short: boolean }) {
  const [copied, setCopied] = useState(false)
  const [selectable, setSelectable] = useState(false)
  const copy = async () => {
    const done = await isCopyFailureHandled(contact.phone)
    if (done) {
      setCopied(true)
      showToast({ message: 'شماره کپی شد', icon: 'check', overSheet: true })
      return
    }
    // F11#22: the clipboard is denied in some WebViews, so the number becomes selectable.
    track('contact_link_failed')
    setSelectable(true)
    showToast({ message: 'کپی نشد', icon: 'alert', overSheet: true })
  }
  // tel: opens only in phone WebViews. Elsewhere only the copy button shows. Unverified on real clients.
  const platform = getWebApp()?.platform
  const canCall = platform === 'ios' || platform === 'android'
  return (
    <>
      {short ? (
        <Alert data-testid="short-warning">
          <ShieldAlert />
          <AlertDescription className="text-foreground">قبل از دیدن خانه پولی نفرستید.</AlertDescription>
        </Alert>
      ) : null}
      {contact.telegram ? (
        <Card className="gap-3 p-4" data-testid="contact-telegram">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] text-muted-foreground">تلگرام</span>
            <bdi dir="ltr" className="text-right">
              @{contact.telegram}
            </bdi>
          </div>
          <Button className="w-full" onClick={() => openExternal(contact.telegram_url)}>
            <Send />
            پیام در تلگرام
          </Button>
        </Card>
      ) : null}
      {contact.phone ? (
        <Card className="gap-3 p-4" data-testid="contact-phone">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] text-muted-foreground">شماره</span>
            {selectable ? <Input readOnly dir="ltr" value={contact.phone} onFocus={(event) => event.target.select()} aria-label="شماره" /> : <bdi dir="ltr" className="text-right">{contact.phone}</bdi>}
          </div>
          <div className="flex items-center gap-2">
            {canCall ? (
              <Button asChild variant="outline" className="flex-1">
                <a href={`tel:${contact.phone.replace(/\s/g, '')}`}>
                  <Phone />
                  زنگ زدن
                </a>
              </Button>
            ) : null}
            <Button variant={copied ? 'secondary' : 'outline'} className="flex-1" onClick={() => void copy()}>
              {copied ? <Check /> : <Copy />}
              {copied ? 'کپی شد' : 'کپی'}
            </Button>
          </div>
        </Card>
      ) : null}
    </>
  )
}

export function ContactFlow({ flow, listing }: { flow: ReturnType<typeof useContactFlow>; listing: Listing }) {
  const { step, setStep, reveal, close } = flow
  const navigate = useNavigate()

  // ADR-5: rechecked when Telegram returns focus after the join. Silent, unless the person taps «عضو شدم».
  const gate = step.name === 'gate' ? step : null
  const joining = useRef(false)
  useEffect(() => {
    if (!gate) return
    const back = () => {
      if (document.visibilityState === 'visible' && joining.current) {
        joining.current = false
        void reveal(gate.entry, { fromGate: 'focus' })
      }
    }
    document.addEventListener('visibilitychange', back)
    return () => document.removeEventListener('visibilitychange', back)
  }, [gate, reveal])

  const similar = () => navigate(`/?city=${listing.metro_slug}&types=${listing.property_type}`)
  const closeButton = (
    <Button variant="outline" className="w-full" onClick={close}>
      بستن
    </Button>
  )
  const retry = (entry: Entry) => (
    <Button className="w-full" disabled={flow.pending} onClick={() => void reveal(entry)}>
      {flow.pending ? <Spinner /> : <RefreshCw />}
      تلاش مجدد
    </Button>
  )

  const regionRule = REGION_RULES[listing.region]
  const warning = step.name === 'warning' ? step : null
  const join = step.name === 'join' ? step : null
  const revealed = step.name === 'revealed' || step.name === 'post_only' ? step : null
  const member = revealed?.contact.membership_checked

  return (
    <>
      <AppSheet
        open={Boolean(warning)}
        onClose={null}
        onBack={close}
        label="قبل از تماس"
        title="قبل از تماس"
        testId="warning-sheet"
        foot={
          <Button
            className="w-full"
            onClick={() => {
              markWarned()
              if (warning) void reveal(warning.entry, { short: false })
              setStep({ name: 'idle' })
            }}
          >
            متوجه شدم، ادامه
          </Button>
        }
      >
        <p className="m-0 text-sm leading-[22px]">{regionRule ? `${WARNING} ${regionRule}` : WARNING}</p>
      </AppSheet>

      <AppSheet
        open={Boolean(gate)}
        onClose={close}
        label="عضویت در کانال"
        title={gateTitle(gate?.channel ?? null, listing)}
        testId="gate-sheet"
        foot={
          gate ? (
            <Button variant="outline" className="w-full" disabled={gate.checking} onClick={() => void reveal(gate.entry, { fromGate: 'tap' })}>
              {gate.checking ? <Spinner /> : null}
              عضو شدم، {gate.entry === 'source' ? 'دیدن پست اصلی' : 'نمایش تماس'}
            </Button>
          ) : null
        }
      >
        {gate?.stillOut ? (
          <Alert variant="destructive" data-testid="still-out">
            <CircleAlert />
            <AlertDescription>هنوز عضو کانال نشده‌اید.</AlertDescription>
          </Alert>
        ) : null}
        <Masked listing={listing} />
        <Card className="gap-3 p-4">
          <div className="flex items-center gap-3">
            <span className="flex size-8 items-center justify-center rounded-lg bg-muted">
              <Megaphone className="size-[18px]" aria-hidden />
            </span>
            <span className="text-sm font-semibold">{channelTitle(gate?.channel ?? null)}</span>
          </div>
          <Button
            className="w-full"
            onClick={() => {
              if (!gate) return
              track('gate_join_tap')
              setStep({ name: 'join', entry: gate.entry, channel: gate.channel, joinUrl: gate.joinUrl })
            }}
          >
            عضویت در کانال
          </Button>
        </Card>
      </AppSheet>

      <AppSheet
        open={Boolean(join)}
        onClose={null}
        onBack={() => join && setStep({ name: 'gate', entry: join.entry, channel: join.channel, joinUrl: join.joinUrl, stillOut: false, checking: false })}
        label="پیوستن به کانال"
        title={channelTitle(join?.channel ?? null)}
        testId="join-sheet"
        foot={
          <Stack>
            <Button
              className="w-full"
              onClick={() => {
                if (!join) return
                joining.current = true
                openExternal(join.joinUrl)
                setStep({ name: 'gate', entry: join.entry, channel: join.channel, joinUrl: join.joinUrl, stillOut: false, checking: false })
              }}
            >
              پیوستن به کانال
            </Button>
            <Button variant="outline" className="w-full" onClick={() => join && setStep({ name: 'gate', entry: join.entry, channel: join.channel, joinUrl: join.joinUrl, stillOut: false, checking: false })}>
              لغو
            </Button>
          </Stack>
        }
      />

      <AppSheet
        open={Boolean(revealed)}
        onClose={close}
        label="تماس با آگهی‌دهنده"
        title="تماس با آگهی‌دهنده"
        testId="contact-sheet"
        foot={
          revealed?.contact.source_url || step.name === 'post_only' ? (
            <Stack>
              {revealed?.contact.source_url ? (
                <Button variant={step.name === 'post_only' ? 'default' : 'outline'} className="w-full" onClick={() => openExternal(revealed.contact.source_url)}>
                  <ExternalLink />
                  دیدن پست اصلی
                </Button>
              ) : null}
              {step.name === 'post_only' ? <BookmarkAction id={listing.id} variant="outline" /> : null}
            </Stack>
          ) : null
        }
      >
        {member ? (
          <div className="flex items-center gap-1.5" data-testid="member-line">
            <CircleCheck className="size-4" aria-hidden />
            <span className="text-[13px] font-semibold">عضو {channelTitle(listing.channel)}</span>
          </div>
        ) : null}
        {step.name === 'post_only' ? (
          <Alert>
            <CircleAlert />
            <AlertDescription className="text-foreground">آیدی یا شماره‌ای در این آگهی نیست.</AlertDescription>
          </Alert>
        ) : null}
        {step.name === 'revealed' ? <Revealed contact={step.contact} short={step.short} /> : null}
      </AppSheet>

      <AppSheet
        open={step.name === 'closed'}
        onClose={null}
        label="این آگهی بسته شد."
        testId="gate-closed"
        foot={
          <Stack>
            <Button
              className="w-full"
              onClick={() => {
                flow.finishClosed()
                similar()
              }}
            >
              دیدن آگهی‌های مشابه
            </Button>
            <Button variant="outline" className="w-full" onClick={flow.finishClosed}>
              بستن
            </Button>
          </Stack>
        }
      >
        <ProblemBody icon={CircleOff} message="این آگهی بسته شد." />
      </AppSheet>

      <AppSheet open={step.name === 'cap'} onClose={null} label="سقف روزانه" testId="gate-cap" foot={closeButton}>
        <ProblemBody icon={Hourglass} message="امروز به سقف دیدن راه تماس رسیدید. فردا دوباره امتحان کنید." />
      </AppSheet>

      <AppSheet open={step.name === 'failed'} onClose={null} label="راه تماس باز نشد." testId="gate-failed" foot={step.name === 'failed' ? <Stack>{retry(step.entry)}{closeButton}</Stack> : null}>
        <ProblemBody icon={CircleAlert} message="راه تماس باز نشد." />
      </AppSheet>

      <AppSheet open={step.name === 'rate'} onClose={null} label="چند لحظه‌ی دیگر" testId="gate-rate" foot={step.name === 'rate' ? <Stack>{retry(step.entry)}{closeButton}</Stack> : null}>
        <ProblemBody icon={Hourglass} message="چند لحظه‌ی دیگر دوباره امتحان کنید." />
      </AppSheet>

      <AppSheet
        open={step.name === 'banned'}
        onClose={null}
        label="عضویت ممکن نیست"
        testId="gate-banned"
        foot={
          <Stack>
            <Button className="w-full" onClick={() => openExternal(SUPPORT_URL)}>
              <LifeBuoy />
              پشتیبانی
            </Button>
            {closeButton}
          </Stack>
        }
      >
        <ProblemBody icon={Ban} message="شما نمی‌توانید عضو این کانال شوید." />
      </AppSheet>

      <AppSheet
        open={step.name === 'nothing'}
        onClose={null}
        label="راه تماسی نیست"
        testId="gate-nothing"
        foot={
          <Stack>
            <BookmarkAction id={listing.id} />
            <Button variant="outline" className="w-full" onClick={similar}>
              آگهی‌های مشابه
            </Button>
            {closeButton}
          </Stack>
        }
      >
        <ProblemBody icon={CircleOff} message="راه تماسی در این آگهی نیست." />
      </AppSheet>
    </>
  )
}

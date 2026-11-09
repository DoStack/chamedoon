import { Ban, Check, CircleAlert, CircleOff, Copy, ExternalLink, Hourglass, LifeBuoy, MessageCircle, Phone, RefreshCw, Send } from 'lucide-react'
import { useCallback, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Contact, GateChannel, JoinChat, Listing } from '@/api/types'
import { JoinSheet } from '@/components/join-sheet'
import { AppSheet } from '@/components/app-sheet'
import { BookmarkAction } from '@/components/bookmark'
import { needsInternetToast, showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { type ApiFailure, meFetch } from '@/lib/api'
import { openExternal, SUPPORT_URL } from '@/lib/links'
import { overrideCard } from '@/lib/overrides'
import { track } from '@/lib/report'
import { getWebApp } from '@/lib/telegram'

type Step =
  | { name: 'idle' }
  | { name: 'gate'; channel: GateChannel | null; chats: JoinChat[]; stillOut: boolean; checking: boolean } // F11#2, #3, #5, #11, #16
  | { name: 'revealed'; contact: Contact } // F11#6, #7, #18, #19, #20, #22
  | { name: 'post_only'; contact: Contact } // F11#8
  | { name: 'nothing' } // F11#17
  | { name: 'closed' } // F11#10
  | { name: 'gone' } // F02#4
  | { name: 'cap' } // F11#12
  | { name: 'failed' } // F11#13
  | { name: 'rate' } // F11#14
  | { name: 'banned' } // F11#15

// Everything between «تماس» and the contact: F11. Screens switch on `code`. The server upserts, so a retry is safe.
export function useContactFlow(listing: Listing | null) {
  const [step, setStep] = useState<Step>({ name: 'idle' })
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  const id = listing?.id

  const reveal = useCallback(
    async (opts: { fromGate?: 'tap' | 'focus' } = {}) => {
      if (inFlight.current || !id) return
      if (opts.fromGate) track('gate_recheck', { via: opts.fromGate })
      inFlight.current = true
      setPending(true)
      if (opts.fromGate) setStep((current) => (current.name === 'gate' ? { ...current, checking: opts.fromGate === 'tap' } : current))
      const result = await meFetch<Contact>(`/api/v1/me/listings/${id}/contact/`, { method: 'POST' })
      inFlight.current = false
      setPending(false)
      if (result.ok) {
        const contact = result.data
        if (!contact.has_any) {
          track('contact_nothing')
          return setStep({ name: 'nothing' })
        }
        if (opts.fromGate) track('join_confirmed', { moment: 'contact', via: opts.fromGate })
        if (contact.gate === 'failed_open') track('join_check_failed', { moment: 'contact' })
        track(contact.gate === 'failed_open' ? 'gate_fail_open' : 'contact_revealed', { gate: contact.gate, listing_id: id, phone: Boolean(contact.phone), telegram: Boolean(contact.telegram), has_source: Boolean(contact.source_url) })
        // has_any covers a source post alone, so a listing with only that reaches F11#8 and its button works.
        if (!contact.telegram && !contact.phone) return setStep({ name: 'post_only', contact })
        return setStep({ name: 'revealed', contact })
      }
      onFailure(result, opts.fromGate)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id],
  )

  const onFailure = (failure: ApiFailure, fromGate?: 'tap' | 'focus') => {
    switch (failure.code) {
      case 'not_member': {
        const channel = failure.channel ?? listing?.channel ?? null
        // An older server sends only the channel's link: one row for it.
        const chats = failure.chats?.length ? failure.chats : [{ kind: 'channel' as const, title: channelTitle(channel), join_url: failure.joinUrl ?? '', photo_url: '' }]
        track(fromGate ? 'gate_still_out' : 'join_gate_shown', { moment: 'contact', kinds: chats.map((chat) => chat.kind).join(',') })
        return setStep({ name: 'gate', channel, chats, stillOut: fromGate === 'tap', checking: false })
      }
      case 'listing_closed':
        track('gate_listing_closed')
        return setStep({ name: 'closed' })
      case 'listing_gone':
        track('contact_gone')
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
        return setStep({ name: 'rate' })
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
        return setStep({ name: 'failed' })
    }
  }

  const start = useCallback(() => {
    if (!listing) return
    // F25#7
    if (navigator.onLine === false) return needsInternetToast()
    track('contact_tap', { listing_id: listing.id })
    if (!listing.has_telegram && !listing.has_phone && !listing.has_source) {
      track('contact_nothing')
      return setStep({ name: 'nothing' })
    }
    void reveal()
  }, [listing, reveal])

  // F11#10 ends in the closed detail, not back at an active page that offers «تماس» again.
  const finishClosed = useCallback(() => {
    if (listing) overrideCard({ ...listing, closed: true })
    setStep({ name: 'idle' })
  }, [listing])

  const close = useCallback(() => {
    track('contact_close')
    setStep({ name: 'idle' })
  }, [])

  return { step, setStep, pending, start, reveal, close, finishClosed }
}

function channelTitle(channel: GateChannel | null) {
  return channel?.title || 'کانال چمدون - خانه'
}

function gatePlace(channel: GateChannel | null, listing: Listing) {
  // The sheet names the listing's own city, or the country when only the country channel is asked for (F11#16).
  if (channel && !channel.city) return listing.country === 'DE' ? 'آلمان' : 'کانادا'
  const name = channel?.title.split(/ [·|-] /).pop()?.trim()
  return name || listing.city_fa || listing.city
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

const DESKTOP = new Set(['tdesktop', 'macos', 'weba', 'webk', 'web', 'unigram'])

function Revealed({ contact }: { contact: Contact }) {
  const [copied, setCopied] = useState(false)
  const [selectable, setSelectable] = useState(false)
  const copy = async () => {
    track('contact_copy')
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
  // tel: does nothing in Telegram's desktop apps, so call shows everywhere else. Unverified on real clients.
  const platform = getWebApp()?.platform ?? ''
  const canCall = !DESKTOP.has(platform)
  // wa.me needs the country code, which a number read from an old Telegram post may lack. Kept off @/lib/phone: this
  // sheet is in the first-load bundle.
  const international = contact.phone.trim().startsWith('+') ? contact.phone.replace(/\D/g, '') : ''
  return (
    <>
      {contact.telegram ? (
        <Card className="ph-no-capture gap-3 p-4" data-testid="contact-telegram">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] text-muted-foreground">تلگرام</span>
            <bdi dir="ltr" className="text-right">
              @{contact.telegram}
            </bdi>
          </div>
          <Button className="w-full" onClick={() => { track('contact_open', { via: 'telegram' }); openExternal(contact.telegram_url) }}>
            <Send />
            پیام در تلگرام
          </Button>
        </Card>
      ) : null}
      {contact.phone ? (
        <Card className="ph-no-capture gap-3 p-4" data-testid="contact-phone">
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] text-muted-foreground">شماره</span>
            {selectable ? <Input readOnly dir="ltr" value={contact.phone} onFocus={(event) => event.target.select()} aria-label="شماره" /> : <bdi dir="ltr" className="text-right">{contact.phone}</bdi>}
          </div>
          <div className="flex items-center gap-2">
            {canCall ? (
              <Button asChild variant="outline" className="flex-1">
                <a href={`tel:${contact.phone.replace(/[^\d+]/g, '')}`} data-testid="contact-call" onClick={() => track('contact_open', { via: 'call' })}>
                  <Phone />
                  زنگ زدن
                </a>
              </Button>
            ) : null}
            <Button variant={copied ? 'secondary' : 'outline'} className="flex-1" onClick={() => void copy()} data-testid="contact-copy">
              {copied ? <Check /> : <Copy />}
              {copied ? 'کپی شد' : 'کپی'}
            </Button>
            {international ? (
              <Button variant="outline" className="flex-1" onClick={() => { track('contact_open', { via: 'whatsapp' }); openExternal(`https://wa.me/${international}`) }} data-testid="contact-whatsapp">
                <MessageCircle />
                واتس‌اپ
              </Button>
            ) : null}
          </div>
        </Card>
      ) : null}
    </>
  )
}

export function ContactFlow({ flow, listing }: { flow: ReturnType<typeof useContactFlow>; listing: Listing }) {
  const { step, reveal, close } = flow
  const navigate = useNavigate()

  // ADR-5: the sheet rechecks when Telegram returns focus after the join. Silent, unless the person taps «عضو شدم».
  const gate = step.name === 'gate' ? step : null
  const recheck = useCallback((via: 'tap' | 'focus') => void reveal({ fromGate: via }), [reveal])
  const lastGate = useRef(gate)
  if (gate) lastGate.current = gate
  const shown = gate ?? lastGate.current

  const similar = () => {
    track('contact_similar')
    navigate(`/?city=${listing.metro_slug}&types=${listing.property_type}`)
  }
  const closeButton = (
    <Button variant="outline" className="w-full" onClick={close}>
      بستن
    </Button>
  )
  const retry = (
    <Button className="w-full" disabled={flow.pending} onClick={() => { track('retry_tap', { screen: 'contact' }); void reveal() }}>
      {flow.pending ? <Spinner /> : <RefreshCw />}
      تلاش مجدد
    </Button>
  )

  const revealed = step.name === 'revealed' || step.name === 'post_only' ? step : null

  return (
    <>
      <JoinSheet
        open={Boolean(gate)}
        moment="contact"
        chats={shown?.chats ?? []}
        place={gatePlace(shown?.channel ?? null, listing)}
        checking={Boolean(gate?.checking)}
        stillOut={Boolean(gate?.stillOut)}
        onConfirm={recheck}
        onClose={() => {
          track('join_gate_skipped', { moment: 'contact' })
          close()
        }}
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
                <Button variant={step.name === 'post_only' ? 'default' : 'outline'} className="w-full" onClick={() => { track('source_open'); openExternal(revealed.contact.source_url) }}>
                  <ExternalLink />
                  دیدن پست اصلی
                </Button>
              ) : null}
              {step.name === 'post_only' ? <BookmarkAction id={listing.id} variant="outline" /> : null}
            </Stack>
          ) : null
        }
      >
        {step.name === 'post_only' ? (
          <Alert>
            <CircleAlert />
            <AlertDescription className="text-foreground">آیدی یا شماره‌ای در این آگهی نیست.</AlertDescription>
          </Alert>
        ) : null}
        {step.name === 'revealed' ? <Revealed contact={step.contact} /> : null}
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

      <AppSheet open={step.name === 'failed'} onClose={null} label="راه تماس باز نشد." testId="gate-failed" foot={<Stack>{retry}{closeButton}</Stack>}>
        <ProblemBody icon={CircleAlert} message="راه تماس باز نشد." />
      </AppSheet>

      <AppSheet open={step.name === 'rate'} onClose={null} label="چند لحظه‌ی دیگر" testId="gate-rate" foot={<Stack>{retry}{closeButton}</Stack>}>
        <ProblemBody icon={Hourglass} message="چند لحظه‌ی دیگر دوباره امتحان کنید." />
      </AppSheet>

      <AppSheet
        open={step.name === 'banned'}
        onClose={null}
        label="عضویت ممکن نیست"
        testId="gate-banned"
        foot={
          <Stack>
            <Button className="w-full" onClick={() => { track('support_open', { from: 'gate_banned' }); openExternal(SUPPORT_URL) }}>
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

import { Plus, RefreshCw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import type { Household, PostResult } from '@/api/post-types'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useSavedCity } from '@/lib/city'
import { clearDraft, currencyOf, hasContact, hasContent, loadDraft, newDraft, priceOk, removeContact, saveDraft, textTooShort, writeDraft, type Draft, type HomeType } from '@/lib/post-draft'
import { track } from '@/lib/report'
import { Chip } from '../filter-sheet'
import { Bad, ChipsMany, ChipsOne, ConfirmSheet, ContactFields, DescriptionField, DoneBody, FootColumn, GoButton, Labeled, MoneyInput, contactOk, PostPage, Stack, Steps, useAreas, usePostUser } from './parts'
import { DuplicateSheet, FailNote, PreviewBody, PublishFoot } from './preview'
import { usePublish } from './use-publish'

const TYPES: ReadonlyArray<readonly [HomeType, string]> = [
  ['room', 'اتاق'],
  ['studio', 'استودیو'],
  ['basement', 'بیسمنت'],
  ['b1', '۱ خوابه'],
  ['b2', '۲ خوابه+'],
]
const WHO: ReadonlyArray<readonly [Exclude<Household, ''>, string]> = [
  ['one', 'یک نفر'],
  ['couple', 'زوج'],
  ['family', 'خانواده'],
  ['two_plus', 'دو نفر یا بیشتر'],
]
const MAX_AREAS = 6

const ERR = {
  budget: 'بودجه را بنویسید',
  short: 'توضیحات کوتاه است. حداقل ۲۰ حرف بنویسید.',
  contact: 'شماره یا آیدی را در متن ننویسید.',
  phone: 'یک راه تماس لازم است. شماره تلفن بگذارید.',
  types: 'نوع خانه را انتخاب کنید.',
}

// Areas from the metro's list, or typed when it has none or fails.
function AreasSheet({ draft, onAdd, onClose }: { draft: Draft; onAdd: (name: string) => void; onClose: () => void }) {
  const areas = useAreas(draft.city?.country, draft.city?.city)
  const [typed, setTyped] = useState('')
  const rows = (areas.data?.areas ?? []).filter((row) => !draft.areas.includes(row.name_fa))
  return (
    <AppSheet
      open
      onClose={onClose}
      label="محله"
      title="محله"
      testId="wanted-areas"
      foot={
        <Button
          className="w-full"
          disabled={!typed.trim()}
          onClick={() => {
            onAdd(typed.trim())
            onClose()
          }}
          data-testid="area-add-typed"
        >
          افزودن
        </Button>
      }
    >
      <Input aria-label="محله" dir="auto" maxLength={40} placeholder="محله" value={typed} onChange={(event) => setTyped(event.target.value)} data-testid="area-typed" />
      {rows.length ? (
        <div className="flex flex-wrap items-center gap-2">
          {rows.map((row) => (
            <Chip
              key={row.id}
              label={row.name_fa}
              on={false}
              onClick={() => {
                onAdd(row.name_fa)
                onClose()
              }}
            />
          ))}
        </div>
      ) : null}
    </AppSheet>
  )
}

export function WantedForm({ initial, editing = null, onConflict, restored = false }: { initial: Draft; editing?: { id: number; updatedAt: string } | null; onConflict?: () => void; restored?: boolean }) {
  const navigate = useNavigate()
  const user = usePostUser()
  const [draft, setDraft] = useState(initial)
  const [step, setStep] = useState(initial.step === 2 ? 2 : 1)
  const [budgetBlurred, setBudgetBlurred] = useState(false)
  const [aboutBlurred, setAboutBlurred] = useState(false)
  const [serverContact, setServerContact] = useState(false)
  const [phoneError, setPhoneError] = useState(false)
  const [areasOpen, setAreasOpen] = useState(false)
  const [saveSheet, setSaveSheet] = useState(false)
  const [saveFailed, setSaveFailed] = useState(false)
  const [done, setDone] = useState<PostResult | null>(null)
  const keep = useRef(!editing)
  const about = useRef<HTMLDivElement>(null)
  const set = (patch: Partial<Draft>) => setDraft((row) => ({ ...row, ...patch }))

  useEffect(() => {
    if (keep.current && hasContent(draft)) writeDraft({ ...draft, step })
  }, [draft, step])

  useEffect(() => {
    track('post_step', { kind: 'wanted', step })
    if (step === 2) track('post_preview', { kind: 'wanted' })
    return () => track('post_step_left', { kind: 'wanted', step })
  }, [step])

  // F19#11
  useEffect(() => {
    if (restored) track('draft_restored', { kind: 'wanted' })
    if (restored) showToast({ id: 'post-restored', message: 'پیش‌نویس شما برگشت.', icon: 'check' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const publish = usePublish({
    draft,
    setDraft,
    hasUsername: Boolean(user.username),
    editing,
    onDone: (result) => {
      keep.current = false
      setDone(result)
    },
    onFields: (fields) => {
      setStep(1)
      if (fields.description) {
        setServerContact(fields.description.includes('contact_in_text'))
        setAboutBlurred(true)
        window.setTimeout(() => about.current?.scrollIntoView({ block: 'center' }), 0)
      }
      if (fields.price) setBudgetBlurred(true)
      if (fields.phone) setPhoneError(true)
      if (fields.country || fields.kind) navigate('/post', { state: { errors: Object.keys(fields) } })
    },
    onEditRefused: (code) => {
      if (code === 'edit_conflict') {
        onConflict?.()
      } else {
        showToast({ message: code === 'listing_closed' ? 'این آگهی بسته شده است' : 'این آگهی دیگر نیست', icon: 'alert' })
        navigate('/mine', { replace: true })
      }
    },
  })

  const contact = serverContact || hasContact(draft.text)
  const short = textTooShort(draft.text)
  const aboutError = contact ? ERR.contact : aboutBlurred && short ? ERR.short : undefined
  const budgetBad = budgetBlurred && !priceOk(draft.price)
  const contactReady = user.ready && contactOk(user.username, draft.phone, draft.city?.country)
  const ready = priceOk(draft.price) && draft.wantedTypes.length > 0 && !short && !contact && contactReady

  // The errors on screen right now, as field:code. Each one is counted when it appears.
  const failKey = (step === 1 ? [contact && 'text:contact_in_text', aboutError === ERR.short && 'text:too_short', budgetBad && 'price:required', user.ready && !contactReady && 'phone:required'] : []).filter(Boolean).join()
  const failing = useRef<string[]>([])
  useEffect(() => {
    const now = failKey ? failKey.split(',') : []
    for (const fail of now) if (!failing.current.includes(fail)) track('post_validation_failed', { kind: 'wanted', step, field: fail.split(':')[0], code: fail.split(':')[1] })
    failing.current = now
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failKey])

  const back = () => {
    if (saveSheet) return setSaveSheet(false)
    if (step === 2) return setStep(1)
    if (editing) return navigate('/mine')
    if (hasContent(draft)) track('draft_ask', { kind: 'wanted' })
    if (hasContent(draft)) return setSaveSheet(true)
    keep.current = false
    clearDraft('wanted')
    navigate('/post')
  }

  const saveAndLeave = async () => {
    if (await saveDraft({ ...draft, step })) {
      track('draft_saved', { kind: 'wanted' })
      keep.current = false
      navigate('/post')
    } else {
      track('draft_save_failed', { kind: 'wanted' })
      setSaveFailed(true)
    }
  }

  if (done) {
    const held = done.post_outcome === 'held'
    return (
      <PostPage nav={editing ? 'me' : 'post'} testId={held ? 'wanted-held' : 'wanted-live'}>
        <DoneBody
          held={held}
          testId="post-done"
          actions={
            <>
              <GoButton to="/mine" testId="done-mine">
                آگهی‌های من
              </GoButton>
              <GoButton to="/" variant="outline" testId="done-search">
                جستجوی خانه
              </GoButton>
            </>
          }
        />
      </PostPage>
    )
  }

  if (step === 2) {
    return (
      <PostPage
        back={back}
        testId="wanted-preview"
        foot={
          <PublishFoot
            phase={publish.phase}
            onEdit={() => {
              publish.reset()
              setStep(1)
            }}
            onPublish={publish.publish}
          />
        }
      >
        <Stack>
          <Steps step={2} total={2} label="پیش‌نمایش" />
          <FailNote phase={publish.phase} />
          <PreviewBody draft={draft} withCard={false} />
        </Stack>
        {publish.joinSheet}
        {publish.duplicate ? <DuplicateSheet duplicate={publish.duplicate} onClose={publish.closeDuplicate} onAnyway={publish.publishAnyway} onUpdate={() => publish.updateOld(publish.duplicate!.id)} onView={() => navigate(`/listing/${publish.duplicate!.id}`)} /> : null}
      </PostPage>
    )
  }

  return (
    <PostPage
      back={back}
      testId="wanted-form"
      foot={
        <FootColumn>
          <Button className="w-full" disabled={!ready} onClick={() => setStep(2)} data-testid="post-next">
            ادامه: پیش‌نمایش
          </Button>
        </FootColumn>
      }
    >
      <Stack gap="gap-3">
        <Steps step={1} total={2} />
        <ContactFields
          username={user.username}
          ready={user.ready}
          phone={draft.phone}
          showPhone
          country={draft.city?.country}
          onPhone={(phone) => {
            setPhoneError(false)
            set({ phone })
          }}
          missingCopy={ERR.phone}
          serverError={phoneError}
        />
        <div onBlur={() => setBudgetBlurred(true)}>
          <Labeled label="بودجه‌ی ماهانه" htmlFor="wanted-budget" error={budgetBad ? ERR.budget : undefined}>
            <MoneyInput id="wanted-budget" code={currencyOf(draft.city?.country)} placeholder="مثلاً تا ۱٬۶۰۰" value={draft.price} invalid={budgetBad} onChange={(price) => set({ price })} />
          </Labeled>
        </div>
        <Labeled label="محله‌ها">
          <div className="flex flex-wrap items-center gap-2" data-testid="wanted-areas-row">
            {draft.areas.map((name) => (
              <Button key={name} variant="secondary" size="sm" aria-label={`حذف ${name}`} onClick={() => { track('wanted_area_remove'); set({ areas: draft.areas.filter((item) => item !== name) }) }}>
                {name}
                <X />
              </Button>
            ))}
            {draft.areas.length < MAX_AREAS ? (
              <Button variant="outline" size="sm" className="border-dashed" onClick={() => { track('wanted_area_add'); setAreasOpen(true) }} data-testid="wanted-area-add">
                <Plus />
                افزودن
              </Button>
            ) : null}
          </div>
        </Labeled>
        <Labeled label="نوع خانه">
          <ChipsMany options={TYPES} value={draft.wantedTypes} onChange={(wantedTypes) => set({ wantedTypes })} label="نوع خانه" />
        </Labeled>
        <Labeled label="تاریخ ورود" htmlFor="wanted-from">
          <Input id="wanted-from" type="date" dir="ltr" value={draft.availableFrom} onChange={(event) => set({ availableFrom: event.target.value })} />
        </Labeled>
        <Labeled label="چه کسی">
          <ChipsOne options={WHO} value={draft.household || null} onChange={(household) => set({ household })} label="چه کسی" />
        </Labeled>
        <div ref={about}>
          <DescriptionField
            id="wanted-about"
            label="درباره‌ی شما"
            rows={2}
            value={draft.text}
            error={aboutError}
            testId="wanted-about"
            onBlur={() => setAboutBlurred(true)}
            onChange={(text) => {
              setServerContact(false)
              set({ text })
            }}
          />
        </div>
        {contact ? (
          <Button
            variant="outline"
            className="w-full"
            data-testid="strip-contact"
            onClick={() => {
              track('post_strip_contact')
              setServerContact(false)
              set({ text: removeContact(draft.text) })
            }}
          >
            <X />
            حذف از متن
          </Button>
        ) : null}
      </Stack>
      {areasOpen ? <AreasSheet draft={draft} onClose={() => setAreasOpen(false)} onAdd={(name) => set({ areas: draft.areas.includes(name) ? draft.areas : [...draft.areas, name].slice(0, MAX_AREAS) })} /> : null}
      {saveSheet ? (
        <ConfirmSheet
          title="پیش‌نویس ذخیره شود؟"
          testId="save-draft"
          onClose={() => setSaveSheet(false)}
          foot={
            <div className="flex flex-col gap-2">
              {saveFailed ? <Bad testId="draft-failed">پیش‌نویس ذخیره نشد</Bad> : null}
              <Button className="w-full" onClick={() => void saveAndLeave()} data-testid="draft-save">
                {saveFailed ? <RefreshCw /> : null}
                {saveFailed ? 'تلاش مجدد' : 'ذخیره پیش‌نویس'}
              </Button>
              <Button
                variant="outline"
                className="w-full"
                data-testid="draft-discard"
                onClick={() => {
                  track('draft_abandoned', { kind: 'wanted', from: 'wizard' })
                  keep.current = false
                  clearDraft('wanted')
                  navigate('/post')
                }}
              >
                حذف و خروج
              </Button>
              <Button variant="outline" className="w-full" onClick={() => { track('draft_keep', { kind: 'wanted' }); setSaveSheet(false) }} data-testid="draft-continue">
                ادامه ثبت آگهی
              </Button>
            </div>
          }
        />
      ) : null}
    </PostPage>
  )
}

// F19: comes back to the draft it left, with its toast (F19#11).
export default function WantedScreen() {
  const saved = useSavedCity()
  const location = useLocation()
  const state = location.state as { city?: Draft['city'] } | null
  const [start] = useState(() => {
    const stored = loadDraft('wanted')
    const city = stored?.city ?? state?.city ?? (saved ? { country: saved.country, city: saved.city, slug: saved.slug, name_fa: saved.name_fa } : null)
    return { draft: stored ?? newDraft('wanted', city), restored: Boolean(stored?.savedAt && hasContent(stored)) }
  })
  if (!start.draft.city) return <Navigate to="/post" replace />
  return <WantedForm initial={start.draft} restored={start.restored} />
}

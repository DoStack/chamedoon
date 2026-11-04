import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronLeft, FileText, KeyRound, MapPin, Search, type LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import type { MineList } from '@/api/post-types'
import { showToast } from '@/components/app-toast'
import { ON_PHOTO } from '@/components/listing-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { FieldError } from '@/components/ui/field'
import { Toggle } from '@/components/ui/toggle'
import { meFetch, unwrap } from '@/lib/api'
import { saveCity, useSavedCity, type SavedCity } from '@/lib/city'
import { fa, relativeTime } from '@/lib/format'
import { clearDraft, clearFresh, draftCard, draftTouchedThisRun, loadDraft, loadFresh, restoreDraft, type Draft, type PostKind } from '@/lib/post-draft'
import { track } from '@/lib/report'
import { CitySheet } from '../city-sheet'
import { PostPage, Stack, StaticCard } from './parts'
import { MINE_KEY } from './use-publish'

const NO_CITY: SavedCity = { country: 'CA', city: '', slug: '', name_fa: '' }

// The toast of F18#24 belongs to reopening the app, once. Coming back from the form in the same run is F18#15.
let toldThisRun = false

function KindTile({ icon: Icon, label, on, onClick, testId }: { icon: LucideIcon; label: string; on?: boolean; onClick: () => void; testId: string }) {
  return (
    <Toggle variant="outline" pressed={Boolean(on)} onPressedChange={onClick} data-testid={testId} className="h-[76px] w-full flex-col gap-0.5 px-1 text-xs font-semibold data-[state=on]:border-primary data-[state=on]:bg-accent data-[state=on]:text-accent-foreground">
      <Icon className="!size-[22px]" aria-hidden />
      <span>{label}</span>
    </Toggle>
  )
}

function DraftCard({ draft, onContinue, onDelete, testPrefix = 'draft' }: { draft: Draft; onContinue: () => void; onDelete: () => void; testPrefix?: string }) {
  const when = draft.savedAt ? relativeTime(new Date(draft.savedAt).toISOString()) : ''
  return (
    <StaticCard
      className="ph-no-capture"
      card={draftCard(draft)}
      badge={
        <Badge variant="outline" className={`absolute top-3 right-3 ${ON_PHOTO}`}>
          پیش‌نویس
        </Badge>
      }
      line={[when, `قدم ${fa(draft.step)} از ۵`].filter(Boolean).join(' · ')}
      action={
        <div className="flex items-center gap-2">
          <Button className="min-w-0 flex-1" onClick={onContinue} data-testid={`${testPrefix}-resume`}>
            ادامه ثبت آگهی
          </Button>
          <Button variant="outline" onClick={onDelete} data-testid={`${testPrefix}-delete`}>
            حذف
          </Button>
        </div>
      }
    />
  )
}

// F18#1, #15, #19, #24: what kind of post, in which city, and the draft if one waits.
export default function ChooseScreen() {
  const navigate = useNavigate()
  const location = useLocation()
  const saved = useSavedCity()
  const [city, setCity] = useState<SavedCity | null>(saved)
  const [sheet, setSheet] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(() => loadDraft('offer'))
  const [unsaved, setUnsaved] = useState<Draft | null>(() => loadFresh())
  const [waiting, setWaiting] = useState<PostKind | null>(null)
  const errors = ((location.state as { errors?: string[] } | null)?.errors ?? []) as string[]
  const [cityError, setCityError] = useState(errors.includes('country'))
  const [kindError, setKindError] = useState(errors.includes('kind'))

  const mine = useQuery({ queryKey: [...MINE_KEY, 'first'], queryFn: () => unwrap(meFetch<MineList>('/api/v1/me/listings/')), retry: false })
  const liveCount = mine.data?.results.filter((row) => row.owner_state === 'live').length ?? 0

  useEffect(() => {
    track('post_choose')
    void restoreDraft('offer').then((found) => {
      setDraft(found)
      if (found && !toldThisRun && !draftTouchedThisRun()) track('draft_restored', { kind: 'offer' })
      if (found && !toldThisRun && !draftTouchedThisRun()) showToast({ id: 'post-restored', message: 'پیش‌نویس شما برگشت.', icon: 'check' })
      toldThisRun = true
    })
  }, [])

  const start = async (kind: PostKind, at = city) => {
    track('post_kind', { kind })
    if (!at?.city) {
      track('post_validation_failed', { kind, step: 'choose', field: 'city', code: 'required' })
      setWaiting(kind)
      setCityError(true)
      return
    }
    setKindError(false)
    const target = { country: at.country, city: at.city, slug: at.slug, name_fa: at.name_fa }
    if (kind === 'wanted') return navigate('/post/wanted', { state: { city: target } })
    // With a draft waiting, a new post is kept apart from it until the person saves. The answer can be in CloudStorage.
    const waitingDraft = await restoreDraft('offer')
    navigate('/post/offer', { state: { fresh: Boolean(waitingDraft), city: target } })
  }

  return (
    <PostPage nav="post" testId="post-choose">
      <Stack>
        <div className="flex flex-col gap-1">
          <div>
            <Button variant="outline" className={cityError && !city?.city ? '!border-destructive !text-destructive' : ''} onClick={() => setSheet(true)} data-testid="post-city">
              <MapPin />
              {city?.name_fa || 'انتخاب شهر'}
              <ChevronDown />
            </Button>
          </div>
          {cityError && !city?.city ? <FieldError data-testid="city-error">کشور را انتخاب کنید.</FieldError> : null}
        </div>
        {draft || unsaved ? (
          <div className="flex flex-col gap-2" data-testid="draft-home">
            <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">پیش‌نویس شما</h3>
            {unsaved ? (
              <DraftCard
                draft={unsaved}
                testPrefix="unsaved"
                onContinue={() => { track('draft_resume', { kind: 'offer' }); navigate('/post/offer', { state: { fresh: true } }) }}
                onDelete={() => {
                  track('draft_abandoned', { kind: 'offer', from: 'choose' })
                  clearFresh()
                  setUnsaved(null)
                }}
              />
            ) : null}
            {draft ? (
              <DraftCard
                draft={draft}
                onContinue={() => { track('draft_resume', { kind: 'offer' }); navigate('/post/offer') }}
                onDelete={() => {
                  track('draft_abandoned', { kind: 'offer', from: 'choose' })
                  clearDraft('offer')
                  setDraft(null)
                }}
              />
            ) : null}
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          {draft || unsaved ? <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">آگهی تازه</h3> : <h1 className="m-0 text-2xl leading-8 font-extrabold">چه آگهی‌ای ثبت می‌کنید؟</h1>}
          <div className="grid grid-cols-2 gap-2" data-testid="post-kinds">
            <KindTile icon={KeyRound} label="خانه دارم" on={waiting === 'offer'} onClick={() => void start('offer')} testId="kind-offer" />
            <KindTile icon={Search} label="دنبال خانه‌ام" on={waiting === 'wanted'} onClick={() => void start('wanted')} testId="kind-wanted" />
          </div>
          {kindError ? <FieldError data-testid="kind-error">نوع آگهی را انتخاب کنید.</FieldError> : null}
        </div>
        <Card className="gap-0 px-4 py-1">
          <button type="button" className="flex min-h-12 w-full items-center gap-3 text-start" onClick={() => { track('post_to_mine'); navigate('/mine') }} data-testid="to-mine">
            <FileText className="size-[18px] flex-none text-muted-foreground" aria-hidden />
            <span className="min-w-0 flex-1 text-sm font-semibold">آگهی‌های من</span>
            {liveCount ? <span className="text-sm text-muted-foreground">{fa(liveCount)} منتشر شده</span> : null}
            <ChevronLeft className="size-4 flex-none text-muted-foreground" aria-hidden />
          </button>
        </Card>
      </Stack>
      <CitySheet
        open={sheet}
        current={city ?? NO_CITY}
        onClose={() => setSheet(false)}
        onPick={(next) => {
          track('post_city', { city: next.slug })
          setCity(next)
          setCityError(false)
          if (!saved) void saveCity(next)
          if (waiting) void start(waiting, next)
        }}
      />
    </PostPage>
  )
}

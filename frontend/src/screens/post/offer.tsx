import { CircleAlert, ImagePlus, Pencil, RefreshCw, TriangleAlert, X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import type { PostParse, PostResult, PriceBasis, PricePeriod, RentalMode, Tri } from '@/api/post-types'
import { AppSheet } from '@/components/app-sheet'
import { needsInternetToast, rateLimitedToast, showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { NativeSelect, NativeSelectOption } from '@/components/ui/native-select'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { ON_PHOTO, Photo } from '@/components/listing-card'
import { meFetch } from '@/lib/api'
import { useSavedCity } from '@/lib/city'
import { currency, fa, formatDate, modeLabel } from '@/lib/format'
import { amount, clearDraft, clearFresh, currencyOf, draftSummary, applyParsed, hasContact, hasContent, loadDraft, loadFresh, missingFields, newDraft, pickType, priceOk, removeContact, restoreDraft, saveDraft, textTooShort, writeDraft, writeFresh, type Draft, type HomeType, type MissingField } from '@/lib/post-draft'
import { MAX_PHOTOS, usePhotoTiles, type Tile } from '@/lib/post-photos'
import { closePosting, useSmartPosting } from '@/lib/posting'
import { track } from '@/lib/report'
import { useCities } from '@/lib/use-cities'
import { CitySheet } from '../city-sheet'
import { SlowNote, useAfter } from '../global-states'
import { BAD_CHIP, Bad, ChipsOne, ConfirmSheet, ContactFields, DescriptionField, DoneBody, FootColumn, GoButton, Labeled, LONG_TYPES, MoneyInput, Pair, phoneOk, PostPage, Stack, Steps, SwitchLine, useAreas, usePostUser } from './parts'
import { DuplicateSheet, FailNote, PreviewBody, PublishFoot } from './preview'
import { usePublish } from './use-publish'

const TOTAL = 5
const RENT: ReadonlyArray<readonly [RentalMode, string]> = [
  ['long_term', 'بلندمدت'],
  ['sublet', 'سابلت'],
  ['lease_transfer', 'لیز ترنسفر'],
  ['short_term', 'کوتاه‌مدت'],
]
const PERIOD: ReadonlyArray<readonly [PricePeriod, string]> = [
  ['month', 'در ماه'],
  ['week', 'در هفته'],
  ['night', 'هر شب'],
]
const BASIS: ReadonlyArray<readonly [PriceBasis, string]> = [
  ['warm', 'گرم'],
  ['cold', 'سرد'],
]
const REG: ReadonlyArray<readonly [Tri, string]> = [
  ['yes', 'ممکن است'],
  ['no', 'ممکن نیست'],
]
const NEEDS: Record<MissingField, string> = { type: 'نوع خانه', price: 'قیمت', area: 'محله', city: 'شهر' }
const PERIOD_LABEL: Record<PricePeriod, string> = { month: 'در ماه', week: 'در هفته', night: 'هر شب' }

const ERR = {
  contact: 'شماره یا آیدی را در متن ننویسید. راه تماس را در قدم بعد وارد کنید.',
  short: 'توضیحات کوتاه است. حداقل ۲۰ حرف بنویسید.',
  type: 'نوع ملک را انتخاب کنید.',
  city: 'شهر را بنویسید.',
  price: 'قیمت را بنویسید.',
  phone: 'شماره تلفن لازم است',
}

function homeOptions(country: string | undefined): ReadonlyArray<readonly [HomeType, string]> {
  if (country !== 'DE') return LONG_TYPES
  return [
    ['room', 'اتاق WG'],
    ['studio', 'استودیو'],
    ['basement', 'بیسمنت'],
    ['b1', '۱ اتاقه'],
    ['b2', '۲ اتاقه'],
    ['b3', '۳ اتاقه+'],
  ]
}

function typeName(draft: Draft) {
  if (draft.homeType === 'house') return 'خانه'
  return homeOptions(draft.city?.country).find(([key]) => key === draft.homeType)?.[1] ?? ''
}

function listJoin(names: string[]) {
  if (names.length < 2) return names.join('')
  return `${names.slice(0, -1).join('، ')} و ${names[names.length - 1]}`
}

function EditChip({ children, bad, onClick, testId }: { children: ReactNode; bad?: boolean; onClick: () => void; testId?: string }) {
  return (
    <Button variant="outline" size="sm" className={bad ? BAD_CHIP : ''} onClick={onClick} data-testid={testId}>
      {children}
      {bad ? null : <Pencil />}
    </Button>
  )
}

function ParsedRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <span className="w-[72px] flex-none pt-1.5 text-[13px] text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

type Section = 'type' | 'mode' | 'area' | 'price' | 'bills' | 'furnished' | 'date' | 'registration' | 'deposit'

const SECTION_TITLE: Record<Section, string> = { type: 'نوع خانه', mode: 'نوع اجاره', area: 'محله', price: 'قیمت', bills: 'قبوض', furnished: 'مبله', date: 'تاریخ', registration: 'ملد', deposit: 'ودیعه' }

// F18#3, #11, #28, #29. A row shows what was read, or what a post still needs.
function ParsedCard({ draft, onEdit }: { draft: Draft; onEdit: (section: Section) => void }) {
  const code = currencyOf(draft.city?.country)
  const german = draft.city?.country === 'DE'
  const price = amount(draft.price)
  const mode = draft.rentalMode && draft.rentalMode !== 'unknown' ? (draft.rentalMode === 'long_term' ? 'بلندمدت' : modeLabel({ rental_mode: draft.rentalMode, country: draft.city?.country ?? '' })) : ''
  return (
    <Card className="gap-2 p-4" data-testid="parsed-card">
      <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">فهمیدم</h3>
      <ParsedRow label="نوع خانه">
        <EditChip bad={!draft.homeType} onClick={() => onEdit('type')} testId="chip-type">
          {draft.homeType ? typeName(draft) : 'انتخاب نوع خانه'}
        </EditChip>
      </ParsedRow>
      {mode ? (
        <ParsedRow label="نوع اجاره">
          <EditChip onClick={() => onEdit('mode')}>{mode}</EditChip>
        </ParsedRow>
      ) : null}
      <ParsedRow label="محله">
        <EditChip bad={!draft.area.trim()} onClick={() => onEdit('area')} testId="chip-area">
          {draft.area.trim() || 'انتخاب محله'}
        </EditChip>
      </ParsedRow>
      <ParsedRow label="قیمت">
        {priceOk(draft.price) ? (
          <>
            <EditChip onClick={() => onEdit('price')} testId="chip-price">
              {fa(price!)} <bdi dir="ltr">{currency(code)}</bdi>
            </EditChip>
            <EditChip onClick={() => onEdit('price')}>{PERIOD_LABEL[draft.pricePeriod]}</EditChip>
            {draft.city?.country === 'DE' && draft.priceBasis !== 'unknown' ? <EditChip onClick={() => onEdit('price')}>{BASIS.find(([key]) => key === draft.priceBasis)?.[1]}</EditChip> : null}
          </>
        ) : (
          <EditChip bad onClick={() => onEdit('price')} testId="chip-price">
            افزودن قیمت
          </EditChip>
        )}
      </ParsedRow>
      {draft.bills === 'yes' || draft.bills === 'no' ? (
        <ParsedRow label="قبوض">
          <EditChip onClick={() => onEdit('bills')}>{draft.bills === 'yes' ? 'داخل اجاره' : 'جدا'}</EditChip>
        </ParsedRow>
      ) : null}
      {draft.furnished !== 'unknown' ? (
        <ParsedRow label="مبله">
          <EditChip onClick={() => onEdit('furnished')}>{{ yes: 'مبله', partial: 'نیمه‌مبله', no: 'بدون مبلمان', unknown: '' }[draft.furnished]}</EditChip>
        </ParsedRow>
      ) : null}
      {german && draft.registration !== 'unknown' ? (
        <ParsedRow label="ملد">
          <EditChip onClick={() => onEdit('registration')}>{draft.registration === 'yes' ? 'ممکن است' : 'ممکن نیست'}</EditChip>
        </ParsedRow>
      ) : null}
      {german && amount(draft.deposit) ? (
        <ParsedRow label="ودیعه">
          <EditChip onClick={() => onEdit('deposit')}>
            {fa(amount(draft.deposit)!)} <bdi dir="ltr">{currency(code)}</bdi>
          </EditChip>
        </ParsedRow>
      ) : null}
      {draft.availableFrom || draft.availableTo || draft.minTerm ? (
        <ParsedRow label="تاریخ">
          {draft.availableFrom ? <EditChip onClick={() => onEdit('date')}>از {formatDate(draft.availableFrom)}</EditChip> : null}
          {draft.availableTo ? <EditChip onClick={() => onEdit('date')}>تا {formatDate(draft.availableTo)}</EditChip> : null}
          {amount(draft.minTerm) ? <EditChip onClick={() => onEdit('date')}>حداقل {fa(amount(draft.minTerm)!)} ماه</EditChip> : null}
        </ParsedRow>
      ) : null}
    </Card>
  )
}

function ParsingCard() {
  return (
    <Card className="gap-2 p-4" data-testid="parsing">
      <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">فهمیدم</h3>
      <div className="flex flex-col gap-2.5">
        {Array.from({ length: 5 }, (_, index) => (
          <div key={index} className="flex items-center gap-2">
            <Skeleton className="h-4 w-14" />
            <Skeleton className="h-6 w-20" />
          </div>
        ))}
      </div>
    </Card>
  )
}

// F18#6: from the metro's areas. A failure, or a city with none yet, leaves a text field.
function AreaPicker({ draft, set, error }: { draft: Draft; set: (patch: Partial<Draft>) => void; error?: string }) {
  const areas = useAreas(draft.city?.country, draft.city?.city)
  const rows = areas.data?.areas ?? []
  const known = rows.find((row) => row.id === draft.areaId) ?? rows.find((row) => row.name_fa === draft.area || row.canonical === draft.area)
  if (areas.isPending && draft.city?.city) return <Skeleton className="h-9 w-full" data-testid="areas-loading" />
  if (!rows.length) {
    return (
      <Labeled label="محله" htmlFor="post-area" error={error}>
        <Input id="post-area" dir="auto" maxLength={64} value={draft.area} onChange={(event) => set({ area: event.target.value, areaId: null })} data-testid="area-input" />
      </Labeled>
    )
  }
  return (
    <Labeled label="محله" htmlFor="post-area" error={error}>
      <NativeSelect
        id="post-area"
        className="w-full"
        data-testid="area-select"
        value={known ? String(known.id) : draft.area ? 'typed' : ''}
        onChange={(event) => {
          const row = rows.find((item) => String(item.id) === event.target.value)
          if (row) set({ area: row.name_fa, areaId: row.id })
          else if (!event.target.value) set({ area: '', areaId: null })
        }}
      >
        <NativeSelectOption value="">انتخاب محله</NativeSelectOption>
        {!known && draft.area ? <NativeSelectOption value="typed">{draft.area}</NativeSelectOption> : null}
        {rows.map((row) => (
          <NativeSelectOption key={row.id} value={String(row.id)}>
            {row.name_fa}
          </NativeSelectOption>
        ))}
      </NativeSelect>
    </Labeled>
  )
}

function SizeField({ draft, set }: { draft: Draft; set: (patch: Partial<Draft>) => void }) {
  return (
    <Labeled label="متراژ" htmlFor="post-size">
      <Input id="post-size" inputMode="numeric" dir="auto" value={draft.size} onChange={(event) => set({ size: event.target.value })} />
    </Labeled>
  )
}

function DateFields({ draft, set }: { draft: Draft; set: (patch: Partial<Draft>) => void }) {
  return (
    <Pair>
      <Labeled label="تاریخ ورود" htmlFor="post-from">
        <Input id="post-from" type="date" dir="ltr" value={draft.availableFrom} onChange={(event) => set({ availableFrom: event.target.value })} />
      </Labeled>
      <Labeled label="تا تاریخ" htmlFor="post-to">
        <Input id="post-to" type="date" dir="ltr" value={draft.availableTo} onChange={(event) => set({ availableTo: event.target.value })} />
      </Labeled>
    </Pair>
  )
}

function SectionFields({ section, draft, set, replace }: { section: Section; draft: Draft; set: (patch: Partial<Draft>) => void; replace: (next: Draft) => void }) {
  const code = currencyOf(draft.city?.country)
  switch (section) {
    case 'type':
      return (
        <>
          <ChipsOne options={homeOptions(draft.city?.country)} value={draft.homeType} onChange={(next) => replace(pickType(draft, next))} label="نوع خانه" />
          <SizeField draft={draft} set={set} />
        </>
      )
    case 'mode':
      return <ChipsOne options={RENT} value={draft.rentalMode} onChange={(rentalMode) => set({ rentalMode })} label="نوع اجاره" />
    case 'area':
      return <AreaPicker draft={draft} set={set} />
    case 'price':
      return (
        <>
          <Labeled label="اجاره" htmlFor="sheet-price">
            <MoneyInput id="sheet-price" code={code} value={draft.price} onChange={(price) => set({ price })} />
          </Labeled>
          <Labeled label="دوره‌ی قیمت">
            <ChipsOne options={PERIOD} value={draft.pricePeriod} onChange={(pricePeriod) => set({ pricePeriod })} label="دوره‌ی قیمت" />
          </Labeled>
          {draft.city?.country === 'DE' ? (
            <Labeled label="گرم یا سرد">
              <ChipsOne options={BASIS} value={draft.priceBasis} onChange={(priceBasis) => set({ priceBasis })} label="گرم یا سرد" />
            </Labeled>
          ) : null}
        </>
      )
    case 'bills':
      return (
        <ChipsOne
          options={[
            ['yes', 'داخل اجاره'],
            ['no', 'جدا'],
          ]}
          value={draft.bills}
          onChange={(bills) => set({ bills })}
          label="قبوض"
        />
      )
    case 'furnished':
      return (
        <ChipsOne
          options={[
            ['yes', 'مبله'],
            ['partial', 'نیمه‌مبله'],
            ['no', 'بدون مبلمان'],
          ]}
          value={draft.furnished}
          onChange={(furnished) => set({ furnished })}
          label="مبله"
        />
      )
    case 'date':
      return (
        <>
          <DateFields draft={draft} set={set} />
          <Labeled label="حداقل (ماه)" htmlFor="post-term">
            <Input id="post-term" inputMode="numeric" dir="auto" value={draft.minTerm} onChange={(event) => set({ minTerm: event.target.value })} />
          </Labeled>
        </>
      )
    case 'registration':
      return <ChipsOne options={REG} value={draft.registration} onChange={(registration) => set({ registration })} label="ملد" />
    case 'deposit':
      return (
        <Labeled label="ودیعه" htmlFor="post-deposit">
          <MoneyInput id="post-deposit" code={code} value={draft.deposit} onChange={(deposit) => set({ deposit })} />
        </Labeled>
      )
  }
}

// F18#4: a sheet for one row. Nothing changes until «ذخیره».
function SectionSheet({ section, draft, onSave, onClose }: { section: Section; draft: Draft; onSave: (next: Draft) => void; onClose: () => void }) {
  const [temp, setTemp] = useState(draft)
  return (
    <AppSheet
      open
      onClose={onClose}
      label={SECTION_TITLE[section]}
      title={SECTION_TITLE[section]}
      testId="section-sheet"
      foot={
        <Button className="w-full" onClick={() => onSave(temp)} data-testid="section-save">
          ذخیره
        </Button>
      }
    >
      <SectionFields section={section} draft={temp} set={(patch) => setTemp((row) => ({ ...row, ...patch }))} replace={setTemp} />
    </AppSheet>
  )
}

function ManualForm({ draft, set, replace, note, touched, onTouch, askCity }: { draft: Draft; set: (patch: Partial<Draft>) => void; replace: (next: Draft) => void; note: boolean; touched: boolean; onTouch: () => void; askCity: boolean }) {
  const code = currencyOf(draft.city?.country)
  const german = draft.city?.country === 'DE'
  const change = (patch: Partial<Draft>) => {
    onTouch()
    set(patch)
  }
  const contact = hasContact(draft.text)
  const textError = contact ? ERR.contact : touched && textTooShort(draft.text) ? ERR.short : undefined
  return (
    <div className="flex flex-col gap-3" data-testid="manual-form">
      {note ? (
        <Alert data-testid="manual-note">
          <TriangleAlert />
          <AlertDescription className="text-foreground">خواندن خودکار متن الان در دسترس نیست. فرم را پر کنید.</AlertDescription>
        </Alert>
      ) : null}
      <Labeled label="نوع خانه" error={touched && !draft.homeType ? ERR.type : undefined} testId="manual-type">
        <ChipsOne
          options={homeOptions(draft.city?.country)}
          value={draft.homeType}
          label="نوع خانه"
          onChange={(next) => {
            onTouch()
            replace(pickType(draft, next))
          }}
        />
      </Labeled>
      {askCity ? (
        <Labeled label="شهر" htmlFor="post-city" error={!draft.city?.city ? ERR.city : undefined}>
          <Input id="post-city" dir="auto" maxLength={64} placeholder="تورنتو" aria-invalid={!draft.city?.city || undefined} value={draft.city?.city ?? ''} onChange={(event) => change({ city: { country: draft.city?.country ?? 'CA', slug: '', city: event.target.value, name_fa: event.target.value } })} data-testid="city-input" />
        </Labeled>
      ) : null}
      <Labeled label="نوع اجاره">
        <ChipsOne options={RENT} value={draft.rentalMode} onChange={(rentalMode) => change({ rentalMode })} label="نوع اجاره" />
      </Labeled>
      <Pair>
        <Labeled label="اجاره" htmlFor="post-price" error={touched && !priceOk(draft.price) ? ERR.price : undefined}>
          <MoneyInput id="post-price" code={code} value={draft.price} onChange={(price) => change({ price })} invalid={touched && !priceOk(draft.price)} />
        </Labeled>
        <SizeField draft={draft} set={change} />
      </Pair>
      <Labeled label="دوره‌ی قیمت">
        <ChipsOne options={PERIOD} value={draft.pricePeriod} onChange={(pricePeriod) => change({ pricePeriod })} label="دوره‌ی قیمت" />
      </Labeled>
      {german ? (
        <>
          <Labeled label="گرم یا سرد">
            <ChipsOne options={BASIS} value={draft.priceBasis} onChange={(priceBasis) => change({ priceBasis })} label="گرم یا سرد" />
          </Labeled>
          <Labeled label="ملد">
            <ChipsOne options={REG} value={draft.registration} onChange={(registration) => change({ registration })} label="ملد" />
          </Labeled>
          <Labeled label="ودیعه" htmlFor="post-deposit">
            <MoneyInput id="post-deposit" code={code} value={draft.deposit} onChange={(deposit) => change({ deposit })} />
          </Labeled>
        </>
      ) : null}
      <Labeled label="آدرس" htmlFor="post-address-manual">
        <Input id="post-address-manual" className="ph-no-capture" dir="auto" maxLength={64} value={draft.address} onChange={(event) => change({ address: event.target.value })} />
      </Labeled>
      <Labeled label="شرایط">
        <div className="flex flex-wrap items-center gap-2">
          <ChipsOne options={[['yes', 'مبله']]} value={draft.furnished === 'yes' ? 'yes' : null} onChange={() => change({ furnished: draft.furnished === 'yes' ? 'unknown' : 'yes' })} label="مبله" />
          <ChipsOne options={[['yes', 'قبوض داخل']]} value={draft.bills === 'yes' ? 'yes' : null} onChange={() => change({ bills: draft.bills === 'yes' ? 'unknown' : 'yes' })} label="قبوض داخل" />
        </div>
      </Labeled>
      <DateFields draft={draft} set={change} />
      <DescriptionField id="post-text-manual" label="متن آگهی" rows={4} value={draft.text} error={textError} onChange={(text) => change({ text })} onBlur={onTouch} testId="manual-text" />
    </div>
  )
}

function PhotoTile({ tile, main, onRetry, onRemove }: { tile: Tile; main: boolean; onRetry: () => void; onRemove: () => void }) {
  const box = 'relative aspect-[4/3] min-w-0 overflow-hidden rounded-lg bg-secondary'
  const removeX = (place: string) => (
    <Button variant="outline" size="icon" aria-label="حذف عکس" className={`${place} ${ON_PHOTO}`} onClick={onRemove}>
      <X />
    </Button>
  )
  if (tile.status === 'uploading') {
    return (
      <div role="img" aria-label={`در حال بارگذاری، ${fa(tile.progress)}٪`} className={box} data-testid="photo-tile" data-status="uploading">
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm font-bold">
          <Spinner />
          {fa(tile.progress)}٪
        </span>
      </div>
    )
  }
  if (tile.status === 'failed' || tile.status === 'bad') {
    const canRetry = tile.status === 'failed'
    return (
      <div className={box} data-testid="photo-tile" data-status={tile.status}>
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 p-2">
          {tile.message ? <span className="text-center text-xs font-semibold text-destructive">{tile.message}</span> : null}
          {canRetry ? (
            <span className="flex max-w-full items-center gap-1">
              <Button variant="outline" size="sm" className={`min-w-0 ${ON_PHOTO}`} onClick={onRetry}>
                <RefreshCw />
                تلاش مجدد
              </Button>
              {removeX('flex-none')}
            </span>
          ) : (
            <Button variant="outline" size="sm" className={ON_PHOTO} onClick={onRemove}>
              <X />
              حذف عکس
            </Button>
          )}
        </span>
      </div>
    )
  }
  return (
    <div className={box} data-testid="photo-tile" data-status="ready">
      <Photo src={tile.photo?.thumb} className="ph-no-capture absolute inset-0 size-full" />
      {removeX('absolute top-1.5 left-1.5')}
      {main ? <Badge className="absolute right-2 bottom-2">عکس اصلی</Badge> : null}
    </div>
  )
}

type WizardProps = { initial: Draft; editing?: { id: number; updatedAt: string } | null; onConflict?: () => void; fresh?: boolean }

export function OfferWizard({ initial, editing = null, onConflict, fresh = false }: WizardProps) {
  const navigate = useNavigate()
  const user = usePostUser()
  const smart = useSmartPosting()
  const cities = useCities()
  const [draft, setDraft] = useState(initial)
  const [parsing, setParsing] = useState(false)
  const [parse, setParse] = useState<Pick<PostParse, 'kind_guess' | 'city_guess'> | null>(null)
  const [serverContact, setServerContact] = useState(false)
  const [blurred, setBlurred] = useState(false)
  const [note, setNote] = useState(false)
  const [touched, setTouched] = useState(false)
  const [askCity, setAskCity] = useState(!initial.city?.city)
  const [sheet, setSheet] = useState<Section | null>(null)
  const [citySheet, setCitySheet] = useState(false)
  const [saveSheet, setSaveSheet] = useState(false)
  const [saveFailed, setSaveFailed] = useState(false)
  const [wantedWaiting, setWantedWaiting] = useState<Draft | null>(null)
  const [phoneError, setPhoneError] = useState(false)
  const [done, setDone] = useState<PostResult | null>(null)
  const keep = useRef(!editing)
  const slowParse = useAfter(3000, parsing)
  const input = useRef<HTMLInputElement>(null)

  const set = (patch: Partial<Draft>) => setDraft((row) => ({ ...row, ...patch }))
  const photos = usePhotoTiles(initial.photos, (next) => set({ photos: next }))

  useEffect(() => {
    if (keep.current && hasContent(draft)) (fresh ? writeFresh : writeDraft)(draft)
  }, [draft])

  useEffect(() => {
    track('post_step', { kind: 'offer', step: draft.step, stage: draft.stage })
    if (draft.step === 5) track('post_preview', { kind: 'offer' })
    return () => track('post_step_left', { kind: 'offer', step: draft.step, stage: draft.stage })
  }, [draft.step, draft.stage])

  const publish = usePublish({
    draft,
    setDraft,
    hasUsername: Boolean(user.username),
    editing,
    fresh,
    onDone: (result) => {
      keep.current = false
      setDone(result)
    },
    onFields: (fields) => {
      if (fields.description) {
        setServerContact(fields.description.includes('contact_in_text'))
        setBlurred(true)
        setTouched(true)
        return set({ step: 1, stage: draft.stage === 'manual' ? 'manual' : 'text' })
      }
      if (fields.city) {
        setAskCity(true)
        setTouched(true)
        return set({ step: 1, stage: 'manual', city: { country: draft.city?.country ?? 'CA', city: '', slug: '', name_fa: '' } })
      }
      if (fields.property_type || fields.price) {
        setTouched(true)
        return set({ step: 1, stage: 'manual' })
      }
      if (fields.phone) {
        setPhoneError(true)
        return set({ step: 4 })
      }
      if (fields.country || fields.kind) {
        keep.current = false
        navigate('/post', { state: { errors: Object.keys(fields) } })
        return
      }
      showToast({ message: 'آگهی ثبت نشد. متن شما ذخیره است.', icon: 'alert' })
    },
    onPhotoGone: () => {
      set({ step: 2 })
      void photos.recheck()
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
  const textError = contact ? ERR.contact : (blurred || draft.stage !== 'text') && short ? ERR.short : undefined
  const changeText = (text: string) => {
    setServerContact(false)
    set({ text })
  }

  // The errors on screen right now, as field:code. Each one is counted when it appears.
  const failKey = (
    draft.step === 4
      ? [user.ready && !user.username && !phoneOk(draft.phone) && 'phone:required']
      : draft.step === 1
        ? [
            contact && 'text:contact_in_text',
            (draft.stage === 'manual' ? touched && short : textError === ERR.short) && !contact && 'text:too_short',
            draft.stage === 'manual' && touched && !draft.homeType && 'type:required',
            draft.stage === 'manual' && touched && !priceOk(draft.price) && 'price:required',
            draft.stage === 'manual' && askCity && !draft.city?.city && 'city:required',
            ...(draft.stage === 'parsed' ? missingFields(draft).map((name) => `${name}:required`) : []),
          ]
        : []
  )
    .filter(Boolean)
    .join()
  const failing = useRef<string[]>([])
  useEffect(() => {
    const now = failKey ? failKey.split(',') : []
    for (const fail of now) if (!failing.current.includes(fail)) track('post_validation_failed', { kind: 'offer', step: draft.step, field: fail.split(':')[0], code: fail.split(':')[1] })
    failing.current = now
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [failKey])

  const toManual = (withNote: boolean) => {
    setNote(withNote)
    setDraft((row) => ({ ...row, stage: 'manual', rentalMode: row.rentalMode ?? 'long_term' }))
  }

  const read = async () => {
    if (contact || short || parsing) return
    if (!draft.city?.country) return navigate('/post', { replace: true })
    track('post_parse_submit', { text_length: draft.text.trim().length })
    setParsing(true)
    const result = await meFetch<PostParse>('/api/v1/me/posts/parse/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: draft.text.trim(), kind: 'offer', country: draft.city.country, city: draft.city.city }),
      timeoutMs: 30_000,
    })
    setParsing(false)
    if (result.ok) {
      const data = result.data
      track('post_parsed', { ok: true, fields_found: Object.values(data.parsed ?? {}).filter((value) => value !== null && value !== '' && value !== 'unknown').length, understood: data.understood, missing: data.missing.length, left: data.parses_left, contact: Boolean(data.contact_in_text) })
      if (data.contact_in_text) return setServerContact(true)
      if (!data.understood) return toManual(true)
      setParse({ kind_guess: data.kind_guess, city_guess: data.city_guess })
      setDraft((row) => ({ ...applyParsed(row, data.parsed), stage: 'parsed' }))
      return
    }
    track('post_parse_failed', { ok: false, code: result.code })
    switch (result.code) {
      case 'validation_failed':
        setBlurred(true)
        break
      case 'rate_limited':
        rateLimitedToast(() => void read(), 'post-parse')
        break
      case 'offline':
      case 'network_error':
        needsInternetToast()
        break
      case 'feature_off':
        closePosting('smart_posting')
        toManual(true)
        break
      default:
        // parse_cap, parse_unavailable and anything else: the form, with the text kept.
        toManual(true)
    }
  }

  const moveToWanted = () => {
    keep.current = false
    writeDraft({ ...newDraft('wanted', draft.city), stage: 'manual', text: draft.text })
    clearOwn()
    navigate('/post/wanted', { replace: true })
  }

  // The saved draft can be only in CloudStorage, and nothing replaces it without asking (F18#26).
  const toWanted = async () => {
    track('post_to_wanted')
    const waiting = await restoreDraft('wanted')
    if (waiting && hasContent(waiting)) setWantedWaiting(waiting)
    else moveToWanted()
  }

  const leave = () => navigate('/post')
  const clearOwn = () => (fresh ? clearFresh() : clearDraft('offer'))

  const back = () => {
    if (saveSheet) return setSaveSheet(false)
    if (draft.step > 1) return set({ step: draft.step - 1 })
    if (draft.stage === 'parsed' && !editing) return set({ stage: 'text' })
    if (draft.stage === 'manual' && smart && !editing) return set({ stage: 'text' })
    if (editing) return navigate('/mine')
    if (hasContent(draft)) track('draft_ask', { kind: 'offer' })
    if (hasContent(draft)) return setSaveSheet(true)
    keep.current = false
    clearOwn()
    leave()
  }

  const discard = () => {
    track('draft_abandoned', { kind: 'offer', from: 'wizard' })
    keep.current = false
    clearOwn()
    for (const tile of photos.tiles) photos.remove(tile.key)
    leave()
  }

  const saveAndLeave = async () => {
    if (await saveDraft(draft)) {
      track('draft_saved', { kind: 'offer' })
      setSaveFailed(false)
      keep.current = false
      if (fresh) clearFresh()
      leave()
    } else {
      track('draft_save_failed', { kind: 'offer' })
      setSaveFailed(true)
    }
  }

  if (done) {
    const held = done.post_outcome === 'held'
    return (
      <PostPage nav={editing ? 'me' : 'post'} testId={held ? 'post-held' : 'post-live'}>
        <DoneBody
          held={held}
          testId="post-done"
          actions={
            <>
              <GoButton to={`/listing/${done.listing_id}`} testId="done-view">
                دیدن آگهی
              </GoButton>
              <GoButton to="/mine" variant="outline" testId="done-mine">
                آگهی‌های من
              </GoButton>
            </>
          }
        />
      </PostPage>
    )
  }

  const missing = missingFields(draft)
  const needs = missing.map((name) => NEEDS[name])
  const parsedReady = !missing.length && !contact && !short
  const manualReady = Boolean(draft.homeType) && priceOk(draft.price) && Boolean(draft.city?.city) && !short && !contact
  const usernameOk = Boolean(user.username) || phoneOk(draft.phone)

  let body: ReactNode
  let foot: ReactNode
  const nextButton = (label: string, disabled: boolean, onClick: () => void, testId = 'post-next') => (
    <FootColumn>
      <Button className="w-full" disabled={disabled} onClick={onClick} data-testid={testId}>
        {label}
      </Button>
    </FootColumn>
  )

  if (draft.step === 1 && draft.stage === 'manual') {
    body = (
      <Stack gap="gap-3">
        <Steps step={1} total={TOTAL} label="متن آگهی" />
        <ManualForm draft={draft} set={set} replace={setDraft} note={note} touched={touched} onTouch={() => setTouched(true)} askCity={askCity} />
      </Stack>
    )
    foot = nextButton('ادامه: عکس‌ها', !manualReady, () => set({ step: 2 }))
  } else if (draft.step === 1 && draft.stage === 'parsed') {
    const guess = parse?.city_guess
    const otherCity = guess && draft.city && guess.city !== draft.city.city
    const guessName = otherCity ? (cities.data?.countries.flatMap((row) => row.cities).find((row) => row.name === guess.city)?.name_fa ?? guess.city) : ''
    body = (
      <Stack gap="gap-3">
        <Steps step={1} total={TOTAL} label="متن آگهی" />
        {parse?.kind_guess === 'wanted' ? (
          <Alert data-testid="kind-mismatch">
            <CircleAlert />
            <AlertDescription className="flex w-full flex-wrap items-center justify-between gap-2">
              <span className="text-foreground">این متن شبیه دنبال خانه است.</span>
              <Button
                size="sm"
                data-testid="to-wanted"
                onClick={() => void toWanted()}
              >
                ثبت دنبال خانه
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {otherCity ? (
          <Alert data-testid="city-mismatch">
            <CircleAlert />
            <AlertDescription className="flex w-full flex-wrap items-center justify-between gap-2">
              <span className="text-foreground">این متن از {guessName} می‌گوید.</span>
              <Button size="sm" data-testid="change-city" onClick={() => setCitySheet(true)}>
                تغییر شهر
              </Button>
            </AlertDescription>
          </Alert>
        ) : null}
        {needs.length ? <Bad testId="parsed-incomplete">{listJoin(needs)} را کامل کنید.</Bad> : null}
        <DescriptionField id="post-text-parsed" label="متن آگهی" rows={3} value={draft.text} error={textError} onChange={changeText} />
        <ParsedCard draft={draft} onEdit={(section) => { track('post_section_edit', { section }); setSheet(section) }} />
      </Stack>
    )
    foot = nextButton('ادامه: عکس‌ها', !parsedReady, () => set({ step: 2 }))
  } else if (draft.step === 1 && parsing) {
    body = (
      <Stack gap="gap-3">
        <Steps step={1} total={TOTAL} label="متن آگهی" />
        {slowParse ? <SlowNote /> : null}
        <Labeled label="متن آگهی" htmlFor="post-text-reading">
          <Textarea id="post-text-reading" className="ph-no-capture" dir="auto" rows={3} readOnly value={draft.text} />
        </Labeled>
        <ParsingCard />
      </Stack>
    )
    foot = nextButton('ادامه: عکس‌ها', true, () => undefined)
  } else if (draft.step === 1) {
    body = (
      <Stack>
        <Steps step={1} total={TOTAL} label="متن آگهی" />
        <DescriptionField id="post-text" label="متن آگهی را بنویسید، مثل گروه" value={draft.text} error={textError} onChange={changeText} onBlur={() => setBlurred(true)} testId="post-text" />
        {contact ? (
          <Button variant="outline" className="w-full" data-testid="strip-contact" onClick={() => { track('post_strip_contact'); changeText(removeContact(draft.text)) }}>
            <X />
            حذف از متن
          </Button>
        ) : null}
      </Stack>
    )
    foot = nextButton('ادامه', short || contact, () => void read())
  } else if (draft.step === 2) {
    body = (
      <Stack>
        <Steps step={2} total={TOTAL} label="عکس‌ها" />
        <div className="flex flex-col gap-2">
          <h3 className="m-0 text-[15px] leading-[22px] font-extrabold">
            عکس‌ها{' '}
            <span className="font-normal text-muted-foreground" data-testid="photo-count">
              ({fa(photos.tiles.length)} از ۶)
            </span>
          </h3>
          <div className="grid grid-cols-2 gap-3">
            {photos.tiles.map((tile, index) => (
              <PhotoTile key={tile.key} tile={tile} main={index === 0} onRetry={() => photos.retry(tile.key)} onRemove={() => photos.remove(tile.key)} />
            ))}
            {photos.tiles.length < MAX_PHOTOS ? (
              <div className="flex aspect-[4/3] items-center justify-center rounded-lg border border-dashed border-input">
                <Button variant="ghost" size="sm" onClick={() => input.current?.click()} data-testid="photo-add">
                  <ImagePlus />
                  افزودن
                </Button>
              </div>
            ) : null}
          </div>
          <input
            ref={input}
            type="file"
            accept="image/*"
            multiple
            hidden
            data-testid="photo-input"
            onChange={(event) => {
              if (event.target.files) photos.addFiles(event.target.files)
              event.target.value = ''
            }}
          />
        </div>
      </Stack>
    )
    foot = nextButton('ادامه: موقعیت', photos.blocked, () => set({ step: 3 }))
  } else if (draft.step === 3) {
    body = (
      <Stack>
        <Steps step={3} total={TOTAL} label="موقعیت" />
        <AreaPicker draft={draft} set={set} />
        <Labeled label="آدرس دقیق" htmlFor="post-address">
          <Input id="post-address" className="ph-no-capture" dir="ltr" maxLength={64} value={draft.address} onChange={(event) => set({ address: event.target.value })} data-testid="address-input" />
        </Labeled>
        <SwitchLine label="نمایش آدرس دقیق در آگهی" checked={draft.showAddress} onChange={(showAddress) => set({ showAddress })} />
      </Stack>
    )
    foot = nextButton('ادامه: راه تماس', !draft.area.trim(), () => set({ step: 4 }))
  } else if (draft.step === 4) {
    body = (
      <Stack>
        <Steps step={4} total={TOTAL} label="راه تماس" />
        <ContactFields
          username={user.username}
          ready={user.ready}
          phone={draft.phone}
          showPhone={draft.showPhone}
          onPhone={(phone) => {
            setPhoneError(false)
            set({ phone })
          }}
          onShowPhone={(showPhone) => set({ showPhone })}
          missingCopy={ERR.phone}
          serverError={phoneError}
        />
      </Stack>
    )
    foot = nextButton('ادامه: پیش‌نمایش', !usernameOk || !user.ready, () => set({ step: 5 }))
  } else {
    body = (
      <div className="flex w-full flex-col gap-3 p-4 md:mx-auto md:max-w-[880px] md:p-6">
        <Steps step={5} total={TOTAL} label="پیش‌نمایش" />
        <FailNote phase={publish.phase} />
        <PreviewBody draft={draft} withCard />
      </div>
    )
    foot = (
      <PublishFoot
        phase={publish.phase}
        onEdit={() => {
          publish.reset()
          set({ step: 1 })
        }}
        onPublish={publish.publish}
      />
    )
  }

  return (
    <PostPage back={back} foot={foot} testId="offer-wizard">
      {body}
      {sheet ? (
        <SectionSheet
          section={sheet}
          draft={draft}
          onClose={() => setSheet(null)}
          onSave={(next) => {
            track('post_section_save', { section: sheet })
            setDraft(next)
            setSheet(null)
          }}
        />
      ) : null}
      {citySheet && draft.city ? (
        <CitySheet
          open
          current={draft.city}
          onClose={() => setCitySheet(false)}
          onPick={(city) => {
            track('post_city', { city: city.slug })
            setParse((row) => (row ? { ...row, city_guess: null } : row))
            set({ city, area: '', areaId: null })
          }}
        />
      ) : null}
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
              <Button variant="outline" className="w-full" onClick={discard} data-testid="draft-discard">
                حذف و خروج
              </Button>
              <Button variant="outline" className="w-full" onClick={() => { track('draft_keep', { kind: 'offer' }); setSaveSheet(false) }} data-testid="draft-continue">
                ادامه ثبت آگهی
              </Button>
            </div>
          }
        >
          {draftSummary(draft) ? <p className="ph-no-capture m-0 text-sm text-muted-foreground">{draftSummary(draft)}</p> : null}
        </ConfirmSheet>
      ) : null}
      {wantedWaiting ? (
        <ConfirmSheet
          title="پیش‌نویس دنبال خانه دارید"
          testId="wanted-waiting"
          onClose={() => setWantedWaiting(null)}
          foot={
            <div className="flex flex-col gap-2">
              <Button
                className="w-full"
                onClick={() => {
                  // The saved one first, so the screen it opens finds it even if only CloudStorage had it.
                  writeDraft(wantedWaiting)
                  navigate('/post/wanted')
                }}
                data-testid="wanted-open-saved"
              >
                ادامه همان پیش‌نویس
              </Button>
              <Button variant="outline" className="w-full" onClick={moveToWanted} data-testid="wanted-replace">
                جایگزینی با این متن
              </Button>
              <Button variant="outline" className="w-full" onClick={() => setWantedWaiting(null)} data-testid="wanted-stay">
                ماندن در این آگهی
              </Button>
            </div>
          }
        >
          {wantedWaiting.text.trim() ? <p className="ph-no-capture m-0 line-clamp-3 text-sm text-muted-foreground">{wantedWaiting.text.trim()}</p> : null}
        </ConfirmSheet>
      ) : null}
      {publish.duplicate ? (
        <DuplicateSheet
          duplicate={publish.duplicate}
          onClose={publish.closeDuplicate}
          onAnyway={publish.publishAnyway}
          onUpdate={() => publish.updateOld(publish.duplicate!.id)}
          onView={() => navigate(`/listing/${publish.duplicate!.id}`)}
        />
      ) : null}
    </PostPage>
  )
}

// F18 from the choice screen: a new post, or the draft it left off at. With smart posting off it starts on the form.
export default function OfferScreen() {
  const saved = useSavedCity()
  const location = useLocation()
  const smart = useSmartPosting()
  const state = location.state as { fresh?: boolean; city?: Draft['city'] } | null
  const [initial] = useState<Draft>(() => {
    const stored = state?.fresh ? loadFresh() : loadDraft('offer')
    const city = stored?.city ?? state?.city ?? (saved ? { country: saved.country, city: saved.city, slug: saved.slug, name_fa: saved.name_fa } : null)
    const base = stored ?? newDraft('offer', city)
    return smart ? base : { ...base, stage: base.stage === 'text' ? 'manual' : base.stage, rentalMode: base.rentalMode ?? 'long_term' }
  })
  if (!initial.city) return <Navigate to="/post" replace />
  return <OfferWizard initial={initial} fresh={Boolean(state?.fresh)} />
}

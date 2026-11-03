import { CircleAlert, Info, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import type { Waitlist } from '@/api/types'
import { AppSheet } from '@/components/app-sheet'
import { showToast } from '@/components/app-toast'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Field, FieldError, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { meFetch } from '@/lib/api'
import { track } from '@/lib/report'

const HINTS = ['اتاوا', 'کلگری', 'مونیخ', 'فرانکفورت']

type Outcome = 'idle' | 'duplicate' | 'failed' | 'listed'

// F01#5, #6, #7, #15, #16, #17. Unique per person and city on the server, so a retry is safe.
export function WaitlistSheet({ open, country, onClose, onPickListed, initial = '' }: { open: boolean; country: string; onClose: () => void; onPickListed: (typed: string) => void; initial?: string }) {
  const [name, setName] = useState(initial)
  const [outcome, setOutcome] = useState<Outcome>('idle')
  const [sending, setSending] = useState(false)
  const typed = name.trim()

  const close = () => {
    setName('')
    setOutcome('idle')
    onClose()
  }

  const submit = async () => {
    setSending(true)
    const result = await meFetch<Waitlist>('/api/v1/me/waitlist/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city_name: typed, ...(country ? { country } : {}) }),
    })
    setSending(false)
    if (result.ok) {
      if (result.data.created) {
        track('waitlist_added')
        showToast({ message: `${typed} به لیست انتظار اضافه شد`, icon: 'check' })
        close()
        return
      }
      track('waitlist_dup')
      setOutcome('duplicate')
      return
    }
    if (result.code === 'validation_failed' && result.fields?.city_name?.includes('city_listed')) {
      setOutcome('listed')
      return
    }
    track('waitlist_failed', { code: result.code })
    setOutcome('failed')
  }

  const backToList = (
    <Button variant={outcome === 'duplicate' ? 'default' : 'ghost'} className="w-full" onClick={close}>
      برگشت به فهرست شهرها
    </Button>
  )
  const foot =
    outcome === 'duplicate' ? (
      backToList
    ) : (
      <div className="flex flex-col gap-2">
        {outcome === 'listed' ? (
          <Button className="w-full" onClick={() => onPickListed(typed)}>
            انتخاب {typed}
          </Button>
        ) : outcome === 'failed' ? (
          <Button className="w-full" disabled={sending} onClick={() => void submit()}>
            {sending ? <Spinner /> : <RefreshCw />}
            تلاش مجدد
          </Button>
        ) : (
          <Button className="w-full" disabled={!typed || sending} onClick={() => void submit()}>
            {sending ? <Spinner /> : null}
            به لیست انتظار اضافه کنید
          </Button>
        )}
        {backToList}
      </div>
    )

  return (
    <AppSheet open={open} onClose={close} label="لیست انتظار" title="شهرتان هنوز در چمدون نیست" foot={foot} testId="waitlist-sheet">
      <Field>
        <FieldLabel htmlFor="waitlist-city">اسم شهر</FieldLabel>
        <Input
          id="waitlist-city"
          value={name}
          placeholder="اتاوا"
          aria-invalid={outcome === 'listed'}
          onChange={(event) => {
            setName(event.target.value)
            setOutcome('idle')
          }}
        />
        {outcome === 'listed' ? <FieldError>{typed} در فهرست هست</FieldError> : null}
      </Field>
      {outcome === 'idle' ? (
        <div className="flex flex-wrap items-center gap-2">
          {HINTS.map((hint) => (
            <Button key={hint} variant="secondary" size="sm" onClick={() => setName(hint)}>
              {hint}
            </Button>
          ))}
        </div>
      ) : null}
      {outcome === 'duplicate' ? (
        <Alert>
          <Info />
          <AlertDescription>{typed} از قبل در لیست انتظار شماست.</AlertDescription>
        </Alert>
      ) : null}
      {outcome === 'failed' ? (
        <Alert variant="destructive">
          <CircleAlert />
          <AlertDescription>ثبت نشد</AlertDescription>
        </Alert>
      ) : null}
    </AppSheet>
  )
}

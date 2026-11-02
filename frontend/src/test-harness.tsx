// Test only, for Playwright (e2e/force-outcome.ts). Only loaded when __TG_MOCK__ is true, so never in Production.
import { useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { type ApiResult, meFetch, publicFetch } from '@/lib/api'

export function Component() {
  const { action } = useParams()
  const [params] = useSearchParams()
  const [result, setResult] = useState<ApiResult<unknown> | null>(null)

  if (action === 'throw') throw new Error('harness raw error text')

  const call = action === 'me' ? meFetch : publicFetch
  return (
    <>
      <button data-testid="run" onClick={() => void call(params.get('path') ?? '').then(setResult)}>
        run
      </button>
      <output data-testid="outcome">{result ? JSON.stringify(result) : ''}</output>
    </>
  )
}

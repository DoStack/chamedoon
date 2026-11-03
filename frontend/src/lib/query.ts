import { QueryClient } from '@tanstack/react-query'
import { isRetryable, type ApiFailureError } from './api'

export const queryClient = new QueryClient({
  defaultOptions: {
    // `always`: offline, a call fails at once and reaches its error state, instead of pausing on a skeleton (F25#1).
    // A timeout has already waited the 15 s of F25#4, so it goes to the error state with no second wait.
    queries: { staleTime: 60_000, networkMode: 'always', retry: (count, error) => count < 1 && isRetryable(error) && (error as ApiFailureError).failure.code !== 'timeout' },
  },
})

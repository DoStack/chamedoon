import { QueryClient } from '@tanstack/react-query'
import { isRetryable } from './api'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, retry: (count, error) => count < 1 && isRetryable(error) },
  },
})

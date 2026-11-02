import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { useUrlFilters } from './url-filters'

const KEYS = ['area', 'bedrooms'] as const

function setup(initial: string) {
  const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  return renderHook(() => ({ ...useUrlFilters(KEYS), search: useLocation().search }), { wrapper })
}

describe('useUrlFilters', () => {
  it('reads repeated keys from the URL', () => {
    const { result } = setup('/?area=a&area=b&src=channel')
    expect(result.current.filters).toEqual({ area: ['a', 'b'], bedrooms: [] })
  })

  it('writes to the URL and keeps other query keys', () => {
    const { result } = setup('/?area=a&src=channel')
    act(() => result.current.setFilter('bedrooms', ['2', '3']))
    expect(result.current.filters.bedrooms).toEqual(['2', '3'])
    expect(result.current.search).toBe('?area=a&src=channel&bedrooms=2&bedrooms=3')
  })

  it('clears a filter from the URL', () => {
    const { result } = setup('/?area=a&src=channel')
    act(() => result.current.setFilter('area', []))
    expect(result.current.search).toBe('?src=channel')
  })
})

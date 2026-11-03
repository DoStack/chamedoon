import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { router } from './App'
import { AppErrorBoundary } from './components/error-boundary'
import { DirectionProvider } from './components/ui/direction'
import { appDir, useLocale } from './lib/locale'
import './index.css'
import { queryClient } from './lib/query'
import { initDensity, initTelegram } from './lib/telegram'
import { Boot } from './screens/boot'

initTelegram()
initDensity()

// `?tgcrash=1` tests the app boundary. __TG_MOCK__ is false in the Production build, which drops this code.
function CrashForTests() {
  if (__TG_MOCK__ && new URLSearchParams(location.search).has('tgcrash')) throw new Error('forced app crash')
  return null
}

function Direction({ children }: { children: ReactNode }) {
  useLocale()
  return <DirectionProvider dir={appDir()}>{children}</DirectionProvider>
}

function render() {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Direction>
        <AppErrorBoundary>
          <CrashForTests />
          <QueryClientProvider client={queryClient}>
            <Boot>
              <RouterProvider router={router} />
            </Boot>
          </QueryClientProvider>
        </AppErrorBoundary>
      </Direction>
    </StrictMode>,
  )
}

// `?tgapi=mock` answers the API in the browser, on Preview and locally, so screens can be seen before their backend ships.
if (__TG_MOCK__ && new URLSearchParams(location.search).get('tgapi') === 'mock') {
  void import('./mocks/api').then(({ installMockApi }) => {
    installMockApi()
    render()
  })
} else {
  render()
}

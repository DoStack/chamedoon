import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router-dom'
import { router } from './App'
import { AppErrorBoundary } from './components/error-boundary'
import { DirectionProvider } from './components/ui/direction'
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

// The courier app has its own flag, token and tabs, and loads only here, so housing's first load stays as it was.
const courier = /^\/app\/v2\/courier(\/|$)/.test(location.pathname)

function render() {
  if (courier) {
    void import('./screens/courier/entry').then(({ mountCourier }) => mountCourier())
    return
  }
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <DirectionProvider dir="rtl">
        <AppErrorBoundary>
          <CrashForTests />
          <QueryClientProvider client={queryClient}>
            <Boot>
              <RouterProvider router={router} />
            </Boot>
          </QueryClientProvider>
        </AppErrorBoundary>
      </DirectionProvider>
    </StrictMode>,
  )
}

// `?tgapi=mock` answers the API in the browser, on Preview and locally, so screens can be seen before their backend ships.
if (__TG_MOCK__ && new URLSearchParams(location.search).get('tgapi') === 'mock') {
  void (courier ? import('./mocks/courier') : import('./mocks/api')).then(({ installMockApi }) => {
    installMockApi()
    render()
  })
} else {
  render()
}

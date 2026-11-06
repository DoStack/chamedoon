import { Fragment, useEffect, useMemo, type ReactNode } from 'react'
import { Toaster } from '@/components/ui/sonner'
import { boot, useAccess } from '@/lib/access'
import { getSavedCity } from '@/lib/city'
import { appDir, useLocale } from '@/lib/locale'
import { SplashScreen } from './splash'
import { BlockedScreen, BootFailedScreen, MaintenanceScreen, OfflineScreen, OutsideScreen, SessionOverScreen, UpdateSheet } from './global-states'

function Frame({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <div className="app-top" />
      <main className="app-main">{children}</main>
      <div className="app-bottom" />
    </div>
  )
}

// Nothing of the app renders until the flag says yes. See lib/access.ts.
export function Boot({ children }: { children: ReactNode }) {
  const { access, update } = useAccess()
  // A change of language redraws the app, since t() reads it during render. In Persian this never changes.
  const locale = useLocale()
  useEffect(() => {
    void boot()
  }, [])
  const blocked = useMemo(() => {
    switch (access.kind) {
      case 'open':
        return null
      case 'outside':
        return <OutsideScreen />
      case 'session_over':
        return <SessionOverScreen />
      case 'blocked':
        return <BlockedScreen />
      case 'maintenance': {
        const city = getSavedCity()
        return <MaintenanceScreen until={access.until} channelName={city ? `کانال ${city.name_fa}` : undefined} channelUrl={city?.channel ? `https://t.me/${city.channel}` : undefined} />
      }
      case 'offline':
        return <OfflineScreen />
      case 'failed':
        return <BootFailedScreen />
      case 'checking':
      case 'legacy':
        return <SplashScreen />
    }
  }, [access])
  return (
    <>
      <Fragment key={locale}>{blocked ? <Frame>{blocked}</Frame> : children}</Fragment>
      <UpdateSheet update={update} />
      <Toaster position="bottom-left" dir={appDir()} offset={{ bottom: 'calc(100dvh - var(--app-h) + var(--toast-b) + var(--safe-b))', left: 24, top: 'calc(var(--safe-t) + 12px)' }} mobileOffset={{ bottom: 'calc(100dvh - var(--app-h) + var(--toast-b) + var(--safe-b))', left: 16, right: 16, top: 'calc(var(--safe-t) + 12px)' }} />
    </>
  )
}

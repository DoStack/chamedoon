import { showToast } from '@/components/app-toast'
import { track } from './report'
import { getWebApp, isVersionAtLeast } from './telegram'

// Same default as home.conf.bot_username. A Production override needs the contract to carry it.
const BOT = (import.meta.env.VITE_HOME_BOT as string | undefined) ?? 'chamedoon_home_bot'

export const SUPPORT_URL = `https://t.me/${BOT}?start=support`

// Post flows stay on the template pages until P4. Hash and search keep Telegram's launch data for them.
export function legacyHomeUrl(path: string) {
  return `/app/home/${path}${location.search}${location.hash}`
}

// The share link: same grammar as home.deeplink, with src-share so it counts apart (P0-16). A listing with no metro has
// no city for the link to name.
export function shareLink(listing: { id: number; country: string; metro_slug: string }) {
  const start = listing.metro_slug ? `home_${listing.country.toLowerCase()}_${listing.metro_slug}_l${listing.id}_src-share` : `home_${listing.id}`
  return `https://t.me/${BOT}?startapp=${start}`
}

export function reopenUrl(startParam: string) {
  return `https://t.me/${BOT}${startParam ? `?startapp=${encodeURIComponent(startParam)}` : ''}`
}

// t.me links stay in Telegram; openLink is for everything else. F25#11 when the client has neither.
export function openExternal(url: string) {
  if (!url.startsWith('https://')) return false // These links come from listings, so no other scheme gets through.
  const app = getWebApp()
  const inTelegram = url.startsWith('https://t.me/')
  const open = inTelegram ? app?.openTelegramLink : app?.openLink
  if (open && (!inTelegram || isVersionAtLeast('6.1'))) {
    open.call(app, url)
    return true
  }
  if (!app?.initData) {
    window.open(url, '_blank', 'noopener')
    return true
  }
  track('client_old', { api: 'openTelegramLink' })
  showToast({ message: 'این بخش در نسخه‌ی تلگرام شما نیست', icon: 'info' })
  return false
}

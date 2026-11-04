import posthog, { type CaptureResult } from 'posthog-js'
import { getSavedCity } from './city'
import { getLocale } from './locale'
import type { Sink } from './report'
import { cleanUrl, scrubData, scrubText } from './scrub'
import { getStartParam, getWebApp } from './telegram'

// The one chunk that knows PostHog. Loaded from report.ts after first paint, only in a Production build with a key.
// `/ingest` is a first-party rewrite in backend/vercel.json, so ad blockers and blocked PostHog hosts lose nothing.

// Generated ids and timings: digit runs in them are not phone numbers.
const LEAVE_ALONE = new Set(['token', 'distinct_id', '$device_id', '$session_id', '$window_id', '$anon_distinct_id', '$user_id', '$insert_id', '$time', '$sent_at', '$lib_version', '$recording_status', '$sdk_debug_replay_internal_buffer_length', 'build_id', 'tg_version', '$browser_version', '$os_version', '$browser_language', '$screen_height', '$screen_width', '$viewport_height', '$viewport_width', '$timezone', '$timezone_offset', '$raw_user_agent'])
const URL_PROPS = /(^|_)(url|href|referrer)$|^\$(current_url|referrer|initial_current_url|initial_referrer|session_entry_url|session_entry_referrer|prev_pageview_pathname)$/i

function cleanProps(input: Record<string, unknown>) {
  const properties: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(input)) {
    if (key === 'detail') continue
    if (LEAVE_ALONE.has(key)) properties[key] = value
    else if (typeof value === 'string' && URL_PROPS.test(key)) properties[key] = value.startsWith('http') || value.startsWith('/') ? cleanUrl(value) : value
    else if (key === '$set' || key === '$set_once') properties[key] = cleanProps((value ?? {}) as Record<string, unknown>)
    else properties[key] = scrubData(value)
  }
  return properties
}

export function scrubEvent(event: CaptureResult | null): CaptureResult | null {
  if (!event) return event
  event.properties = cleanProps(event.properties ?? {}) as typeof event.properties
  if (event.$set) event.$set = cleanProps(event.$set) as typeof event.$set
  if (event.$set_once) event.$set_once = cleanProps(event.$set_once) as typeof event.$set_once
  return event
}

// A deep link can carry an outreach token. The app's own link shapes pass, anything else keeps its first word only.
export function launchSource(start: string) {
  if (!start) return 'direct'
  if (/^(home|qa|courier|courier_alerts|demand|supply|requests|matches|explore|support)$/.test(start)) return start
  const shape = start.match(/^(home_\d+|request_\d+|match_\d+|c(?:ountry)?_[a-z]{2}\b.*|[a-z]{2}(?:_[a-z-]+)*(?:_src-[a-z0-9-]+)?)$/i)
  return shape && start.length <= 64 ? start : `${start.split(/[_-]/)[0].slice(0, 16)}_other`
}

const TELEGRAM_EVENTS = ['activated', 'deactivated', 'themeChanged', 'fullscreenChanged', 'backButtonClicked', 'settingsButtonClicked', 'shareMessageSent', 'shareMessageFailed', 'homeScreenAdded', 'homeScreenFailed', 'locationRequested', 'writeAccessRequested', 'contactRequested', 'popupClosed']

function watchTelegram() {
  const app = getWebApp()
  for (const name of TELEGRAM_EVENTS) {
    app?.onEvent?.(name, () => posthog.capture('tg_lifecycle', { tg_event: name }))
  }
  window.addEventListener('online', () => posthog.capture('network_online'))
  window.addEventListener('offline', () => posthog.capture('network_offline'))
  document.addEventListener('visibilitychange', () => posthog.capture('visibility_changed', { state: document.visibilityState }))
}

export function startPostHog(): Sink {
  const courier = /^\/app\/v2\/courier(\/|$)/.test(location.pathname)
  const app = getWebApp()
  const city = getSavedCity()
  const start = getStartParam()
  posthog.init(__PH_KEY__, {
    api_host: '/ingest',
    ui_host: __PH_UI_HOST__,
    defaults: '2026-08-30',
    capture_pageview: 'history_change',
    capture_pageleave: true,
    autocapture: { capture_copied_text: false },
    rageclick: true,
    capture_dead_clicks: true,
    capture_heatmaps: true,
    capture_performance: { web_vitals: true, network_timing: true },
    capture_exceptions: true,
    enable_recording_console_log: true,
    before_send: scrubEvent,
    session_recording: {
      maskAllInputs: true,
      // Every text node passes through the scrub: the screen stays readable, phone numbers and usernames in free text do not.
      maskTextSelector: '*',
      maskTextFn: (text) => scrubText(text),
      blockClass: 'ph-no-capture',
      recordHeaders: false,
      recordBody: false,
      maskCapturedNetworkRequestFn: (request) => {
        if (request.name?.includes('/ingest/')) return null
        return { ...request, name: request.name ? cleanUrl(request.name) : request.name, requestHeaders: undefined, responseHeaders: undefined, requestBody: undefined, responseBody: undefined }
      },
    },
    loaded: (ph) => {
      ph.register({
        side: courier ? 'courier' : 'housing',
        language: getLocale(),
        country: city?.country ?? null,
        city: city?.slug ?? null,
        tg_platform: app?.platform ?? 'outside',
        tg_version: app?.version ?? null,
        tg_color_scheme: app?.colorScheme ?? null,
        build_id: __BUILD_ID__,
        launch_source: launchSource(start),
      })
    },
  })
  watchTelegram()
  posthog.capture('app_opened', { load_ms: Math.round(performance.now()), first_paint_ms: Math.round(performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0) })
  return {
    event: (name, data, at) => {
      // Courier events pass the route's side (DEMAND, SUPPLY), which would overwrite the app's `side` on that event.
      const { side, ...rest } = data ?? {}
      posthog.capture(name, side === undefined ? rest : { ...rest, route_side: side }, { timestamp: new Date(at) })
    },
    context: (props) => posthog.register(props),
    identify: (telegramId) => {
      const language = getLocale()
      posthog.identify(`tg_${telegramId}`, { language, tg_platform: app?.platform ?? 'outside' }, { first_side: courier ? 'courier' : 'housing', first_launch_source: launchSource(start) })
    },
  }
}

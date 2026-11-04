import { scrubData } from './scrub'

// The one place failures and flow events are logged and handed to PostHog (ADR-9). Never pass tokens or initData.
// PostHog loads after first paint from its own chunk (lib/telemetry-client.ts), so events wait here with their real time.
// No key in the build (tests, mocks, local, Preview): nothing is queued, nothing loads, nothing leaves.
type Props = Record<string, unknown>
export type Sink = { event: (name: string, data: Props | undefined, at: number) => void; context: (props: Props) => void; identify: (telegramId: string | number) => void }

const QUEUE_MAX = 500
let sink: Sink | null = null
const queue: Array<(s: Sink) => void> = []

function send(call: (s: Sink) => void) {
  if (!__PH_KEY__) return
  try {
    if (sink) call(sink)
    else if (queue.length < QUEUE_MAX) queue.push(call)
  } catch {
    // Telemetry never breaks the app.
  }
}

function emit(name: string, data?: Props) {
  const at = Date.now()
  send((s) => s.event(name, data, at))
}

export function report(event: string, data?: Props) {
  console.warn(`[chamedoon] ${event}`, scrubData(data ?? {}))
  emit(event, { ...data, kind: 'failure' })
}

// Names come from the Event column of the flow matrices (exec/FLOW-AUDIT-HOUSING.md), taxonomy in exec/posthog-2026-11-04/EVENTS.md.
export function track(event: string, data?: Props) {
  console.info(`[chamedoon] ${event}`, scrubData(data ?? {}))
  emit(event, data)
}

// City, country, language: stays on every event after this call.
export function setTelemetryContext(props: Props) {
  send((s) => s.context(props))
}

// Only after the server has checked this person's initData.
export function identifyTelemetry(telegramId: string | number) {
  send((s) => s.identify(telegramId))
}

export function startTelemetry() {
  if (!__PH_KEY__) return
  const load = () =>
    void import('./telemetry-client')
      .then(({ startPostHog }) => {
        sink = startPostHog()
        for (const call of queue.splice(0)) send(call)
      })
      .catch(() => {
        queue.length = 0
      })
  // After the first paint and when the phone is free, so startup in Telegram stays as it was.
  const idle = () => (window.requestIdleCallback ? window.requestIdleCallback(load, { timeout: 2500 }) : setTimeout(load, 800))
  if (document.readyState === 'complete') idle()
  else window.addEventListener('load', idle, { once: true })
}

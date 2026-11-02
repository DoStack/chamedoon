// The one place failures are logged. PostHog plugs in here (ADR-9). Never pass tokens or initData.
export function report(event: string, data?: Record<string, unknown>) {
  console.warn(`[chamedoon] ${event}`, data ?? {})
}

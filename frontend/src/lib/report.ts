// The one place failures and flow events are logged. PostHog plugs in here (ADR-9). Never pass tokens or initData.
export function report(event: string, data?: Record<string, unknown>) {
  console.warn(`[chamedoon] ${event}`, data ?? {})
}

// Names come from the Event column of the flow matrices (exec/FLOW-AUDIT-HOUSING.md).
export function track(event: string, data?: Record<string, unknown>) {
  console.info(`[chamedoon] ${event}`, data ?? {})
}

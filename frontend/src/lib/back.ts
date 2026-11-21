import { useEffect, useRef } from 'react'
import { track } from './report'
import { getWebApp } from './telegram'

// The route for telemetry: no base, no ids.
export const routeNow = () => location.pathname.replace(/^\/app\/home/, '').replace(/\d+/g, ':id') || '/'

// Back closes the overlay, then the step, then the page, then the app (BRIEF). Each open thing pushes its handler, and
// Telegram's button runs the newest. With none left the button hides, so Telegram's own close takes over.
const stack: Array<{ current: () => void }> = []

function press() {
  track('back_tap', { from: routeNow() })
  stack[stack.length - 1]?.current()
}

let wired = false
function sync() {
  const button = getWebApp()?.BackButton
  if (!button) return
  if (!wired) {
    button.onClick(press)
    wired = true
  }
  if (stack.length) button.show()
  else button.hide()
}

export function useBack(handler: (() => void) | null) {
  const latest = useRef(handler)
  latest.current = handler
  const on = handler !== null
  useEffect(() => {
    if (!on) return
    const entry = { current: () => latest.current?.() }
    stack.push(entry)
    sync()
    return () => {
      stack.splice(stack.indexOf(entry), 1)
      sync()
    }
  }, [on])
}

// Web mode (a normal browser, no Telegram): an open sheet owns one browser history entry, so the browser's Back closes
// the sheet before it leaves the page. Closing the sheet any other way takes its entry back off, unless the page has
// navigated since (then the entry is left alone, so a navigation is never undone).
type SheetEntry = { id: string; close: { current: () => void }; popped: boolean }
const sheets: SheetEntry[] = []
let quiet = 0
let listening = false

const inBrowser = () => typeof window !== 'undefined' && !getWebApp()?.initData

function onPop() {
  if (quiet > 0) {
    quiet -= 1
    return
  }
  const top = sheets[sheets.length - 1]
  if (!top) return
  top.popped = true
  sheets.pop()
  track('back_tap', { from: routeNow(), browser: true })
  top.close.current()
}

export function useBrowserSheetBack(handler: (() => void) | null) {
  const latest = useRef(handler)
  latest.current = handler
  const on = handler !== null
  useEffect(() => {
    if (!on || !inBrowser()) return
    if (!listening) {
      window.addEventListener('popstate', onPop)
      listening = true
    }
    const entry: SheetEntry = { id: `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`, close: { current: () => latest.current?.() }, popped: false }
    history.pushState({ ...(history.state ?? {}), chmSheet: entry.id }, '')
    sheets.push(entry)
    return () => {
      const at = sheets.indexOf(entry)
      if (at >= 0) sheets.splice(at, 1)
      if (!entry.popped && history.state?.chmSheet === entry.id) {
        quiet += 1
        history.back()
      }
    }
  }, [on])
}

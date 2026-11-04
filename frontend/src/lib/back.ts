import { useEffect, useRef } from 'react'
import { track } from './report'
import { getWebApp } from './telegram'

// The route for telemetry: no base, no ids.
export const routeNow = () => location.pathname.replace(/^\/app\/v2/, '').replace(/\d+/g, ':id') || '/'

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

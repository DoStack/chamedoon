import { useCallback, useEffect, useRef } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from './utils'

// Mouse on a desktop webview: click-drag and the wheel scroll a row the way a finger does. Touch and pen are left to the browser.
export function dragScroll(box: HTMLElement) {
  let down: { x: number; left: number; id: number } | null = null
  let moved = false
  let lastWheel = 0

  const onDown = (e: PointerEvent) => {
    if (e.pointerType !== 'mouse' || e.button !== 0 || box.scrollWidth <= box.clientWidth) return
    down = { x: e.clientX, left: box.scrollLeft, id: e.pointerId }
    moved = false
  }
  const onMove = (e: PointerEvent) => {
    if (!down || e.pointerId !== down.id) return
    const dx = e.clientX - down.x
    if (!moved && Math.abs(dx) > 4) {
      moved = true
      box.style.scrollSnapType = 'none'
      box.setPointerCapture(e.pointerId)
    }
    if (moved) box.scrollLeft = down.left - dx
  }
  const onUp = (e: PointerEvent) => {
    if (!down || e.pointerId !== down.id) return
    down = null
    if (!moved) return
    box.style.scrollSnapType = ''
    // Snap again from where the drag ended.
    box.scrollBy({ left: 0 })
  }
  // A drag is never a click.
  const onClick = (e: MouseEvent) => {
    if (!moved) return
    moved = false
    e.preventDefault()
    e.stopPropagation()
  }
  // A link around the row would start the browser's own drag and cancel ours; a plain click still goes through.
  const onMouseDown = (e: MouseEvent) => {
    if (e.button === 0 && box.scrollWidth > box.clientWidth) e.preventDefault()
  }
  const onWheel = (e: WheelEvent) => {
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX) || box.scrollWidth <= box.clientWidth) return
    e.preventDefault()
    const style = getComputedStyle(box)
    const sign = (e.deltaY > 0 ? 1 : -1) * (style.direction === 'rtl' ? -1 : 1)
    if (style.scrollSnapType !== 'none') {
      const now = Date.now()
      if (now - lastWheel < 350) return
      lastWheel = now
      box.scrollBy({ left: sign * box.clientWidth })
    } else box.scrollLeft += sign * Math.abs(e.deltaY)
  }
  box.addEventListener('pointerdown', onDown)
  box.addEventListener('pointermove', onMove)
  box.addEventListener('pointerup', onUp)
  box.addEventListener('click', onClick, true)
  box.addEventListener('mousedown', onMouseDown)
  box.addEventListener('wheel', onWheel, { passive: false })
  return () => {
    box.removeEventListener('pointerdown', onDown)
    box.removeEventListener('pointermove', onMove)
    box.removeEventListener('pointerup', onUp)
    box.removeEventListener('click', onClick, true)
    box.removeEventListener('mousedown', onMouseDown)
    box.removeEventListener('wheel', onWheel)
  }
}

// A callback ref for rows that own no ref of their own.
export function useDragScroll() {
  const off = useRef<(() => void) | null>(null)
  return useCallback((box: HTMLElement | null) => {
    off.current?.()
    off.current = box ? dragScroll(box) : null
  }, [])
}

export function useDragScrollOn(ref: React.RefObject<HTMLElement | null>) {
  useEffect(() => (ref.current ? dragScroll(ref.current) : undefined), [ref])
}

const FINE = '[@media(hover:hover)_and_(pointer:fine)]:flex'
const ARROW = `absolute top-1/2 z-10 hidden size-8 -translate-y-1/2 items-center justify-center rounded-full bg-background/85 text-foreground shadow ${FINE}`

// Prev/next for a photo strip, only where a mouse is. Next lies at the end side, so it follows the reading direction.
export function StripArrows({ index, count, go }: { index: number; count: number; go: (to: number) => void }) {
  if (count < 2) return null
  const step = (to: number) => (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    go(to)
  }
  return (
    <>
      {index > 0 ? (
        <button type="button" aria-label="قبلی" onClick={step(index - 1)} className={cn(ARROW, 'start-1.5')} data-testid="strip-prev">
          <ChevronRight className="size-5 -scale-x-100 rtl:scale-x-100" />
        </button>
      ) : null}
      {index < count - 1 ? (
        <button type="button" aria-label="بعدی" onClick={step(index + 1)} className={cn(ARROW, 'end-1.5')} data-testid="strip-next">
          <ChevronRight className="size-5 rtl:-scale-x-100" />
        </button>
      ) : null}
    </>
  )
}

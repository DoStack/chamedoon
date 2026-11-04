import { useLayoutEffect, useRef, useState, type MouseEvent } from 'react'
import { track } from './report'

export const BARE_SCROLL = '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden'
// Native touch scrolling: the browser tells a swipe from a vertical scroll, and `dir` sets which side is first.
export const STRIP = `${BARE_SCROLL} flex snap-x snap-mandatory overflow-x-auto overflow-y-hidden overscroll-x-contain`
export const SLIDE = 'size-full flex-none snap-start snap-always'

// A row of slides, one in view. Loads the one in view and its neighbours, and those only once the first has settled,
// which a lazy first image does only when the row comes near the screen.
export function useStrip(screen: string, start = 0, onIndex?: (index: number) => void) {
  const ref = useRef<HTMLDivElement>(null)
  const at = useRef(start)
  const jumpTo = useRef<number | null>(null)
  const downAt = useRef(0)
  const [index, setIndex] = useState(start)
  const [seen, setSeen] = useState([start])
  const [ready, setReady] = useState(false)

  // Measured, not computed, so it holds in both directions. Instant, so a far jump does not load all between.
  // `scrollTo`, since `scrollBy` counts as a directional scroll, which `snap-always` stops at the next slide.
  const go = (to: number) => {
    const box = ref.current
    const slide = box?.children[to]
    if (!box || !slide) return
    jumpTo.current = to
    box.scrollTo({ left: box.scrollLeft + slide.getBoundingClientRect().left - box.getBoundingClientRect().left })
  }

  useLayoutEffect(() => {
    if (start) go(start)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    ref,
    index,
    go,
    shows: (i: number) => i === start || (ready && seen.some((s) => Math.abs(s - i) <= 1)),
    settle: () => setReady(true),
    props: {
      onScroll: () => {
        const box = ref.current
        if (!box?.clientWidth) return
        // An RTL strip scrolls in negative numbers.
        const next = Math.round(Math.abs(box.scrollLeft) / box.clientWidth)
        if (next === at.current) {
          jumpTo.current = null
          return
        }
        at.current = next
        setIndex(next)
        setSeen((old) => [...old, next])
        onIndex?.(next)
        if (jumpTo.current === next) jumpTo.current = null
        else track('photo_swipe', { index: next, screen })
      },
      onPointerDown: () => {
        downAt.current = ref.current?.scrollLeft ?? 0
      },
      // A swipe is never a tap, whatever the webview does at its end.
      onClickCapture: (event: MouseEvent) => {
        if (Math.abs((ref.current?.scrollLeft ?? 0) - downAt.current) > 4) {
          event.preventDefault()
          event.stopPropagation()
        }
      },
    },
  }
}

import { SlowNote, useAfter } from './global-states'

// The logo index.html paints before the app loads, read once before React replaces it, so the bundle carries no copy.
const mark = typeof document === 'undefined' ? '' : (document.querySelector('#root > .splash')?.innerHTML ?? '')

// The boot wait: the same frame as index.html, so the hand-over does not flash.
// F25#4: the wait message at 3 s. The answer or an error comes by 15 s.
export function SplashScreen() {
  const slow = useAfter(3000, true)
  return (
    <div className="splash" data-testid="checking">
      <div className="contents" dangerouslySetInnerHTML={{ __html: mark }} />
      {slow ? <div className="splash-note"><SlowNote /></div> : null}
    </div>
  )
}

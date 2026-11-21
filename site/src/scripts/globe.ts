// Courier globe: cobe (MIT, ~5 KB). Orange pins on Iranian cities, navy arcs to the six diaspora cities.
import createGlobe from 'cobe';

type P = [number, number];
const IRAN: P[] = [[35.69, 51.39], [29.59, 52.58], [36.3, 59.6]];
const ABROAD: P[] = [[43.65, -79.38], [45.5, -73.57], [49.28, -123.12], [52.52, 13.405], [53.55, 9.99], [50.94, 6.96]];

export function mountGlobe(canvas: HTMLCanvasElement, opts: { poster?: boolean } = {}) {
  const size = canvas.getBoundingClientRect().width || 520;
  const dpr = opts.poster ? 2 : Math.min(1.5, window.devicePixelRatio || 1);
  // Start with Iran ↔ Europe in view, then rotate west toward Canada.
  let phi = 4.95;
  let dragging = false, lastX = 0, velocity = 0.0032;
  const globe = createGlobe(canvas, {
    devicePixelRatio: dpr,
    width: size * dpr,
    height: size * dpr,
    phi,
    theta: 0.42,
    dark: 0,
    diffuse: 1.1,
    scale: 1,
    mapSamples: 14000,
    mapBrightness: 5.5,
    mapBaseBrightness: 0.02,
    baseColor: [0.98, 0.93, 0.88],
    markerColor: [0.72, 0.25, 0.05],
    glowColor: [1, 0.95, 0.9],
    markers: [...IRAN.map((l) => ({ location: l, size: 0.06 })), ...ABROAD.map((l) => ({ location: l, size: 0.045, color: [0.04, 0.12, 0.2] as [number, number, number] }))],
    arcs: IRAN.slice(0, 1).flatMap((from) => ABROAD.map((to) => ({ from, to }))).concat([{ from: IRAN[1], to: ABROAD[0] }, { from: IRAN[2], to: ABROAD[3] }]),
    arcColor: [0.04, 0.12, 0.2],
    arcWidth: 0.6,
    arcHeight: 0.35,
    markerElevation: 0.01,
    opacity: 0.95,
  });
  if (opts.poster) {
    let k = 0;
    const tick = () => { globe.update({ phi }); if (++k < 30) requestAnimationFrame(tick); else (window as any).__globeReady = true; };
    requestAnimationFrame(tick);
    canvas.classList.add('ready');
    return;
  }
  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', (e) => { dragging = true; lastX = e.clientX; canvas.setPointerCapture(e.pointerId); canvas.style.cursor = 'grabbing'; });
  canvas.addEventListener('pointerup', () => { dragging = false; canvas.style.cursor = 'grab'; });
  canvas.addEventListener('pointermove', (e) => { if (!dragging) return; phi += (e.clientX - lastX) / 180; lastX = e.clientX; });
  let visible = true, raf = 0;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && !raf) raf = requestAnimationFrame(loop); }).observe(canvas);
  function loop() {
    raf = 0;
    if (!visible || document.hidden) return;
    if (!dragging) phi += velocity;
    globe.update({ phi });
    raf = requestAnimationFrame(loop);
  }
  raf = requestAnimationFrame(loop);
  canvas.classList.add('ready');
}

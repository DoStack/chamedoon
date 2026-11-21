// Site chrome: mobile menu, header shadow, sound toggle (off by default), number tickers.
// Sounds are synthesised with Web Audio (no audio files, no licences) and only after the visitor turns them on.
let ctx: AudioContext | null = null;
let soundOn = false;

function tone(kind: 'zip' | 'pop' | 'pin') {
  if (!soundOn || !ctx) return;
  const t = ctx.currentTime;
  const g = ctx.createGain();
  g.connect(ctx.destination);
  if (kind === 'zip') {
    // Short filtered noise sweep, like a zipper.
    const len = 0.22;
    const buf = ctx.createBuffer(1, Math.floor(ctx.sampleRate * len), ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (i % 220 < 90 ? 1 : 0.25);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    const f = ctx.createBiquadFilter();
    f.type = 'bandpass';
    f.frequency.setValueAtTime(1800, t);
    f.frequency.exponentialRampToValueAtTime(5200, t + len);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + len);
    src.connect(f).connect(g);
    src.start(t);
    src.stop(t + len);
  } else {
    const o = ctx.createOscillator();
    o.type = 'sine';
    const f0 = kind === 'pop' ? 660 : 520;
    o.frequency.setValueAtTime(f0, t);
    o.frequency.exponentialRampToValueAtTime(kind === 'pop' ? 990 : 260, t + 0.12);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.12, t + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
    o.connect(g);
    o.start(t);
    o.stop(t + 0.18);
  }
}

function setSound(on: boolean, btn: HTMLElement) {
  soundOn = on;
  btn.setAttribute('aria-pressed', String(on));
  btn.querySelector('[data-ico-on]')?.toggleAttribute('hidden', !on);
  btn.querySelector('[data-ico-off]')?.toggleAttribute('hidden', on);
  if (on && !ctx) ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
  if (on) tone('pop');
}

function tickers() {
  const els = document.querySelectorAll<HTMLElement>('[data-count]');
  if (!els.length || !('IntersectionObserver' in window)) return;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const fmt = (lang: string) => new Intl.NumberFormat(lang === 'en' ? 'en-CA' : 'fa-IR');
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      const el = e.target as HTMLElement;
      const target = Number(el.dataset.count);
      if (!Number.isFinite(target) || target <= 0) continue;
      const nf = fmt(document.documentElement.lang);
      const final = el.textContent;
      const start = performance.now();
      const dur = 900;
      const step = (now: number) => {
        const k = Math.min(1, (now - start) / dur);
        const eased = 1 - Math.pow(1 - k, 3);
        el.textContent = nf.format(Math.round(target * eased));
        if (k < 1) requestAnimationFrame(step);
        else el.textContent = final;
      };
      requestAnimationFrame(step);
    }
  }, { threshold: 0.6 });
  els.forEach((el) => io.observe(el));
}

export function initChrome() {
  const header = document.querySelector<HTMLElement>('[data-header]');
  const menu = document.querySelector<HTMLButtonElement>('[data-menu]');
  const nav = document.getElementById('site-nav');
  menu?.addEventListener('click', () => {
    const open = menu.getAttribute('aria-expanded') !== 'true';
    menu.setAttribute('aria-expanded', String(open));
    nav?.classList.toggle('open', open);
  });
  const onScroll = () => header?.classList.toggle('scrolled', window.scrollY > 8);
  addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  const btn = document.querySelector<HTMLElement>('[data-sound-toggle]');
  if (btn) {
    let saved = false;
    try { saved = sessionStorage.getItem('chm-sound') === '1'; } catch {}
    if (saved) { soundOn = true; btn.setAttribute('aria-pressed', 'true'); btn.querySelector('[data-ico-on]')?.removeAttribute('hidden'); btn.querySelector('[data-ico-off]')?.setAttribute('hidden', ''); }
    btn.addEventListener('click', () => {
      const on = btn.getAttribute('aria-pressed') !== 'true';
      setSound(on, btn);
      try { sessionStorage.setItem('chm-sound', on ? '1' : '0'); } catch {}
    });
  }
  document.addEventListener('click', (e) => {
    const el = (e.target as HTMLElement).closest('.btn, [data-sound="pin"]');
    if (!el || !soundOn) return;
    if (!ctx) ctx = new AudioContext();
    tone(el.matches('[data-sound="pin"]') ? 'pin' : 'zip');
  });
  document.addEventListener('pointerover', (e) => {
    const el = (e.target as HTMLElement).closest('[data-sound="pop"]');
    if (el && soundOn && !(el as any)._popped) { (el as any)._popped = true; if (!ctx) ctx = new AudioContext(); tone('pop'); setTimeout(() => ((el as any)._popped = false), 600); }
  });
  tickers();
}

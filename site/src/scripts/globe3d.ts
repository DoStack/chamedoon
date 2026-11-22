// Courier globe (three.js r187, shares the three chunk with suitcase.ts). Land, borders and the three highlighted
// countries are drawn at runtime into an equirectangular CanvasTexture from /media/site/geo/world-110m.bin
// (world-atlas, ISC; built by scripts/geo.mjs). Routes are great-circle arcs with height, planes fly both ways,
// and the camera moves per courier step. City labels are HTML (server-rendered, per language), placed each frame.
import {
  WebGLRenderer, Scene, PerspectiveCamera, Mesh, SphereGeometry, MeshStandardMaterial, MeshBasicMaterial, CanvasTexture,
  SRGBColorSpace, AmbientLight, DirectionalLight, Vector3, Matrix4, Curve, TubeGeometry, Shape, ShapeGeometry,
  CircleGeometry, InstancedMesh, Color, Sprite, SpriteMaterial, DoubleSide,
} from 'three';

type Opts = { poster?: boolean };
export type GlobeApi = { setStep(i: number): void };

const D2R = Math.PI / 180;
const ORANGE = '#b8410c', NAVY = '#1d3a5c';
const ll2v = (lat: number, lon: number, out = new Vector3()) =>
  out.set(Math.cos(lat * D2R) * Math.cos(lon * D2R), Math.sin(lat * D2R), -Math.cos(lat * D2R) * Math.sin(lon * D2R));
const v2lat = (v: Vector3) => Math.asin(Math.max(-1, Math.min(1, v.y / v.length()))) / D2R;
const v2lon = (v: Vector3) => Math.atan2(-v.z, v.x) / D2R;
const smooth = (a: number, b: number, x: number) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

// ---- geo file -------------------------------------------------------------------------------------------------
function decode(buf: ArrayBuffer): Int32Array[][] {
  const b = new Uint8Array(buf);
  let i = 0;
  const vi = () => { let n = 0, s = 0, c = 0; do { c = b[i++]; n |= (c & 127) << s; s += 7; } while (c & 128); return n >>> 0; };
  const unz = (n: number) => (n >>> 1) ^ -(n & 1);
  const layers: Int32Array[][] = [];
  const L = b[i++];
  for (let l = 0; l < L; l++) {
    const rings: Int32Array[] = [];
    for (let r = 0, rc = vi(); r < rc; r++) {
      const pc = vi(), a = new Int32Array(pc * 2);
      let x = 0, y = 0;
      for (let p = 0; p < pc; p++) { x += unz(vi()); y += unz(vi()); a[p * 2] = x; a[p * 2 + 1] = y; }
      rings.push(a);
    }
    layers.push(rings);
  }
  return layers;
}

function drawMap(layers: Int32Array[][], W: number) {
  const H = W / 2, c = document.createElement('canvas');
  c.width = W; c.height = H;
  const g = c.getContext('2d')!;
  const sx = W / 18000, sy = H / 9000, k = W / 2048;
  const path = (rings: Int32Array[], close: boolean) => {
    g.beginPath();
    for (const r of rings) {
      let mn = Infinity, mx = -Infinity;
      for (let i = 0; i < r.length; i += 2) { mn = Math.min(mn, r[i]); mx = Math.max(mx, r[i]); }
      const offs = [0];
      if (mn < 0) offs.push(18000);
      if (mx > 18000) offs.push(-18000);
      for (const o of offs) {
        for (let i = 0; i < r.length; i += 2) {
          const x = (r[i] + o) * sx, y = r[i + 1] * sy;
          if (i) g.lineTo(x, y); else g.moveTo(x, y);
        }
        if (close) g.closePath();
      }
    }
  };
  g.fillStyle = '#f5efe6';
  g.fillRect(0, 0, W, H);
  g.strokeStyle = 'rgba(160,130,95,0.16)';
  g.lineWidth = k;
  g.beginPath();
  for (let lon = 15; lon < 360; lon += 15) { g.moveTo((lon / 360) * W, 0); g.lineTo((lon / 360) * W, H); }
  for (let lat = 15; lat < 180; lat += 15) { g.moveTo(0, (lat / 180) * H); g.lineTo(W, (lat / 180) * H); }
  g.stroke();
  const [land, borders, ir, ca, de] = layers;
  g.lineJoin = 'round';
  path(land, true); g.fillStyle = '#e2d6c4'; g.fill('evenodd');
  g.strokeStyle = '#c4b29a'; g.lineWidth = 1.1 * k; g.stroke();
  path([...ca, ...de], true); g.fillStyle = '#cbd8e6'; g.fill('evenodd');
  path(ir, true); g.fillStyle = '#f4c7ae'; g.fill('evenodd');
  path(borders, false); g.strokeStyle = 'rgba(120,96,70,0.55)'; g.lineWidth = 0.9 * k; g.stroke();
  path([...ca, ...de], true); g.strokeStyle = NAVY; g.lineWidth = 1.3 * k; g.stroke();
  path(ir, true); g.strokeStyle = ORANGE; g.lineWidth = 1.8 * k; g.stroke();
  return c;
}

// ---- routes ---------------------------------------------------------------------------------------------------
class Arc extends Curve<Vector3> {
  ang: number;
  constructor(public a: Vector3, public b: Vector3, public h: number) { super(); this.ang = a.angleTo(b); }
  getPoint(t: number, out = new Vector3()) {
    const s = Math.sin(this.ang), wa = Math.sin((1 - t) * this.ang) / s, wb = Math.sin(t * this.ang) / s;
    return out.set(this.a.x * wa + this.b.x * wb, this.a.y * wa + this.b.y * wb, this.a.z * wa + this.b.z * wb)
      .multiplyScalar(1 + this.h * Math.sin(Math.PI * t));
  }
}

const OUT: [string, string][] = [['tehran', 'toronto'], ['tehran', 'berlin'], ['shiraz', 'vancouver'], ['isfahan', 'hamburg'], ['mashhad', 'montreal'], ['tabriz', 'cologne']];
const IN: [string, string][] = [['toronto', 'tehran'], ['montreal', 'shiraz'], ['vancouver', 'tehran'], ['berlin', 'isfahan'], ['hamburg', 'tabriz'], ['cologne', 'mashhad']];
const HERO = 0, HERO_BACK = OUT.length;
const FOLLOW = 7.5;

// Camera per step: lat, lon, distance from the globe centre (radius 1). Step 3 follows the hero plane.
const VIEWS = [
  { lat: 47, lon: -42, dist: 3.25 },
  { lat: 33, lon: 54, dist: 2.1 },
  { lat: 46, lon: -14, dist: 3.85 },
  { lat: 0, lon: 0, dist: 2.75 },
];
const LABELS = [
  ['toronto', 'montreal', 'vancouver', 'berlin', 'hamburg', 'cologne'],
  ['tehran', 'isfahan', 'shiraz', 'mashhad', 'tabriz'],
  ['tehran', 'toronto', 'berlin', 'vancouver'],
  ['toronto', 'tehran'],
];

function planeGeometry() {
  const p = [[0, 1], [0.13, 0.78], [0.13, 0.28], [0.98, -0.12], [0.98, -0.3], [0.13, -0.08], [0.11, -0.62], [0.42, -0.84], [0.42, -0.98], [0, -0.88]];
  const s = new Shape();
  s.moveTo(p[0][0], p[0][1]);
  for (let i = 1; i < p.length; i++) s.lineTo(p[i][0], p[i][1]);
  for (let i = p.length - 2; i > 0; i--) s.lineTo(-p[i][0], p[i][1]);
  s.closePath();
  return new ShapeGeometry(s);
}

function haloTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d')!;
  const gr = g.createRadialGradient(128, 128, 78, 128, 128, 128);
  gr.addColorStop(0, 'rgba(184,65,12,0.16)');
  gr.addColorStop(0.35, 'rgba(240,190,160,0.12)');
  gr.addColorStop(1, 'rgba(250,250,248,0)');
  g.fillStyle = gr;
  g.fillRect(0, 0, 256, 256);
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  return t;
}

type Label = { el: HTMLElement; key: string; v: Vector3; w: number; h: number };

export async function mountGlobe(stage: HTMLElement, opts: Opts = {}): Promise<GlobeApi> {
  const canvas = stage.querySelector('canvas')!;
  const nav = stage.querySelector<HTMLElement>('[data-stepnav]');
  const poster = !!opts.poster;
  const geo = fetch('/media/site/geo/world-110m.bin').then((r) => { if (!r.ok) throw new Error('geo'); return r.arrayBuffer(); });

  const renderer = new WebGLRenderer({ canvas, antialias: false, alpha: true, powerPreference: 'low-power' });
  renderer.setClearColor(0x000000, 0);
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.setPixelRatio(poster ? 2 : Math.min(1.5, window.devicePixelRatio || 1));
  const scene = new Scene();
  const camera = new PerspectiveCamera(32, 1, 0.05, 50);
  scene.add(camera);
  scene.add(new AmbientLight(0xffffff, 2.55));
  const sun = new DirectionalLight(0xffffff, 0.75);
  sun.position.set(-1.2, 1.4, 2);
  camera.add(sun);

  const halo = new Sprite(new SpriteMaterial({ map: haloTexture(), depthWrite: false, transparent: true }));
  halo.scale.setScalar(2.7);
  halo.renderOrder = -1;
  halo.visible = !poster; // a soft alpha halo bands badly in the AVIF poster
  scene.add(halo);

  const coarse = matchMedia('(pointer: coarse)').matches || innerWidth < 800;
  const W = Math.min(poster || !coarse ? 4096 : 2048, renderer.capabilities.maxTextureSize);
  const map = new CanvasTexture(drawMap(decode(await geo), W));
  map.colorSpace = SRGBColorSpace;
  map.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
  const globe = new Mesh(new SphereGeometry(1, 96, 64), new MeshStandardMaterial({ map, roughness: 1, metalness: 0 }));
  scene.add(globe);

  // Cities: HTML labels from the server markup, 3D dots on the surface.
  const labels: Label[] = [...stage.querySelectorAll<HTMLElement>('[data-city]')].map((el) => ({
    el, key: el.dataset.city!, v: ll2v(+el.dataset.lat!, +el.dataset.lng!), w: 0, h: 0,
  }));
  const city = (k: string) => labels.find((l) => l.key === k)!.v;
  const dotGeo = new CircleGeometry(1, 20);
  const dotBase = new InstancedMesh(dotGeo, new MeshBasicMaterial({ color: 0xffffff }), labels.length);
  const dotCore = new InstancedMesh(dotGeo, new MeshBasicMaterial({ color: 0xffffff }), labels.length);
  labels.forEach((l, i) => dotCore.setColorAt(i, new Color(l.el.dataset.side === 'ir' ? ORANGE : NAVY)));
  scene.add(dotBase, dotCore);

  // Arcs: outbound (Iran → abroad) orange and higher, inbound (abroad → Iran) navy and lower.
  const routes = [...OUT.map((r) => ({ r, out: true })), ...IN.map((r) => ({ r, out: false }))].map(({ r, out }, i) => {
    const a = city(r[0]), b = city(r[1]);
    const ang = a.angleTo(b);
    const curve = new Arc(a, b, (0.05 + ang * 0.13) * (out ? 1 : 0.72));
    const mat = new MeshBasicMaterial({ color: out ? ORANGE : NAVY, transparent: true, opacity: 0.5, depthWrite: false });
    const geom = new TubeGeometry(curve, 120, 0.0034, 5, false);
    const mesh = new Mesh(geom, mat);
    scene.add(mesh);
    return { curve, mat, geom, mesh, out, alpha: 0.5, phase: (i * 0.37) % 1, dur: 7 + ang * 3.2, idx: geom.index!.count };
  });

  const pGeo = planeGeometry();
  const planes = new InstancedMesh(pGeo, new MeshBasicMaterial({ color: 0xffffff, side: DoubleSide }), routes.length);
  const outline = new InstancedMesh(pGeo, new MeshBasicMaterial({ color: 0xffffff, side: DoubleSide, depthWrite: false }), routes.length);
  routes.forEach((r, i) => planes.setColorAt(i, new Color(r.out ? ORANGE : NAVY)));
  outline.renderOrder = 1;
  planes.renderOrder = 2;
  scene.add(outline, planes);

  // ---- state ----
  let step = poster ? 2 : 0;
  let stepAt = 0;
  const cam = { lat: VIEWS[step].lat, lon: VIEWS[step].lon, dist: VIEWS[step].dist + 1.2 };
  const user = { lon: 0, lat: 0 };
  let time = poster ? 3.4 : 0;
  let followP = 0;
  let width = 1, height = 1, obstacle: DOMRect | null = null, size = 1;

  const measure = () => {
    const r = stage.getBoundingClientRect();
    width = Math.max(1, r.width); height = Math.max(1, r.height);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    for (const l of labels) { l.w = l.el.offsetWidth; l.h = l.el.offsetHeight; }
    if (nav && nav.offsetParent) {
      const n = nav.getBoundingClientRect();
      obstacle = new DOMRect(n.left - r.left - 6, n.top - r.top - 6, n.width + 12, n.height + 12);
    } else obstacle = null;
  };
  measure();
  new ResizeObserver(() => { measure(); kick(); }).observe(stage);
  document.fonts?.ready.then(() => { measure(); kick(); });

  const m4 = new Matrix4(), tmp = new Vector3(), tmp2 = new Vector3(), up = new Vector3(), fx = new Vector3(), fy = new Vector3(), fz = new Vector3();

  function placePlane(i: number, p: number, scale: number) {
    const r = routes[i];
    r.curve.getPoint(Math.max(0.0001, Math.min(0.9999, p)), tmp);
    r.curve.getPoint(Math.min(1, p + 0.004), tmp2);
    fy.subVectors(tmp2, tmp).normalize();
    up.copy(tmp).normalize();
    fx.crossVectors(fy, up).normalize();
    fz.crossVectors(fx, fy);
    tmp.addScaledVector(up, 0.006);
    const s = 0.024 * scale * size;
    m4.makeBasis(fx.multiplyScalar(s), fy.multiplyScalar(s), fz.multiplyScalar(s)).setPosition(tmp);
    planes.setMatrixAt(i, m4);
    m4.makeBasis(fx.multiplyScalar(1.45), fy.multiplyScalar(1.45), fz.multiplyScalar(1.45)).setPosition(tmp.addScaledVector(up, -0.002));
    outline.setMatrixAt(i, m4);
  }

  function frame(dt: number) {
    time += dt;
    const t = time;
    // Hero flight for step 4: one pass, then the plane waits at the destination.
    followP = ease(Math.min(1, Math.max(0, (t - stepAt - 0.6) / FOLLOW)));
    // Camera target
    let tLat: number, tLon: number, tDist: number;
    if (step === 3) {
      routes[HERO].curve.getPoint(Math.max(0.02, followP), tmp);
      tLat = v2lat(tmp) - 6 * (1 - followP); tLon = v2lon(tmp); tDist = VIEWS[3].dist + 0.35 * Math.sin(Math.PI * followP) - 0.25 * followP;
    } else {
      const v = VIEWS[step];
      const drift = poster ? 0 : Math.sin(t * 0.12) * 3;
      tLat = v.lat; tLon = v.lon + drift; tDist = v.dist + (poster ? 0.45 : 0);
    }
    tLat = Math.max(-70, Math.min(75, tLat + user.lat));
    tLon += user.lon;
    const aspect = width / height;
    tDist = 1 + (tDist - 1) * (aspect < 1 ? Math.min(1.6, 1 / aspect) : 1);
    const kf = poster ? 1 : 1 - Math.exp(-dt * (step === 3 ? 3 : 2.2));
    let dl = ((tLon - cam.lon + 540) % 360) - 180;
    cam.lon += dl * kf; cam.lat += (tLat - cam.lat) * kf; cam.dist += (tDist - cam.dist) * kf;
    ll2v(cam.lat, cam.lon, camera.position).multiplyScalar(cam.dist);
    camera.lookAt(0, 0, 0);
    // Planes and dots keep a steady on-screen size when the camera zooms in.
    size = Math.max(0.32, Math.min(1.15, (cam.dist - 1) / 2.4));

    // Arcs
    const pulse = 0.5 + 0.5 * Math.sin(t * 4);
    routes.forEach((r, i) => {
      let a: number;
      if (step === 0) a = r.out ? 0.22 : 0.95;
      else if (step === 1) a = 0.3;
      else if (step === 2) a = i === HERO || i === HERO_BACK ? 0.55 + 0.45 * pulse : 0.2;
      else a = i === HERO ? 1 : 0.12;
      r.alpha += (a - r.alpha) * (poster ? 1 : 1 - Math.exp(-dt * 4));
      r.mat.opacity = r.alpha;
      const full = r.idx;
      r.geom.setDrawRange(0, step === 3 && i === HERO ? Math.max(6, Math.floor((followP * full) / 30) * 30) : full);
      // Planes: staggered loops; in step 4 only the hero flies, the others park out of sight.
      if (step === 3) {
        if (i === HERO) placePlane(i, followP, followP >= 1 ? 1 : 1.55);
        else placePlane(i, 0, 0);
      } else {
        const p = (t / r.dur + r.phase) % 1;
        const env = smooth(0, 0.06, p) * (1 - smooth(0.94, 1, p));
        placePlane(i, p, env * (r.alpha > 0.5 ? 1.15 : 0.85));
      }
    });
    planes.instanceMatrix.needsUpdate = true;
    outline.instanceMatrix.needsUpdate = true;

    // City dots + labels
    const hot = LABELS[step];
    const camN = tmp2.copy(camera.position);
    const placed: DOMRect[] = [];
    if (obstacle) placed.push(obstacle);
    const screen: { l: Label; x: number; y: number; vis: boolean }[] = [];
    labels.forEach((l, i) => {
      const n = l.v;
      fz.copy(n);
      fx.set(0, 1, 0).cross(n).normalize();
      if (fx.lengthSq() < 0.1) fx.set(1, 0, 0);
      fy.crossVectors(n, fx);
      const lead = (step === 3 && l.key === 'toronto' && followP >= 1) || (step === 2 && (l.key === 'tehran' || l.key === 'toronto'));
      const s = 0.011 * size * (lead ? 1 + 0.35 * pulse : 1) * (step === 1 && l.el.dataset.side === 'ir' ? 0.8 : 1);
      tmp.copy(n).multiplyScalar(1.0015);
      m4.makeBasis(fx.clone().multiplyScalar(s * 1.6), fy.clone().multiplyScalar(s * 1.6), fz).setPosition(tmp);
      dotBase.setMatrixAt(i, m4);
      tmp.copy(n).multiplyScalar(1.002);
      m4.makeBasis(fx.multiplyScalar(s), fy.multiplyScalar(s), fz).setPosition(tmp);
      dotCore.setMatrixAt(i, m4);
      const vis = n.dot(camN) > 1.04;
      tmp.copy(n).project(camera);
      screen.push({ l, x: (tmp.x * 0.5 + 0.5) * width, y: (0.5 - tmp.y * 0.5) * height, vis });
      if (vis && hot.includes(l.key)) placed.push(new DOMRect(screen[i].x - 4, screen[i].y - 4, 8, 8));
    });
    dotBase.instanceMatrix.needsUpdate = true;
    dotCore.instanceMatrix.needsUpdate = true;
    if (!poster) {
      const order = [...screen].sort((a, b) => rank(a.l.key) - rank(b.l.key));
      for (const s of order) {
        const want = s.vis && hot.includes(s.l.key);
        let pos: DOMRect | null = null;
        if (want) {
          const { w, h } = s.l;
          const right = new DOMRect(s.x + 9, s.y - h / 2, w, h), left = new DOMRect(s.x - 9 - w, s.y - h / 2, w, h);
          const cands = [...(s.x > width / 2 ? [right, left] : [left, right]), new DOMRect(s.x - w / 2, s.y - h - 9, w, h), new DOMRect(s.x - w / 2, s.y + 9, w, h)];
          pos = cands.find((c) => c.left >= 4 && c.top >= 4 && c.right <= width - 4 && c.bottom <= height - 4 && !placed.some((p) => hit(p, c))) || null;
        }
        if (pos) {
          placed.push(pos);
          s.l.el.style.transform = `translate3d(${pos.left.toFixed(1)}px,${pos.top.toFixed(1)}px,0)`;
          s.l.el.classList.add('on');
          s.l.el.classList.toggle('hot', step === 3 && s.l.key === 'toronto' && followP >= 1);
        } else s.l.el.classList.remove('on', 'hot');
      }
    }
    renderer.render(scene, camera);
  }
  const rank = (k: string) => { const i = LABELS[step].indexOf(k); return i < 0 ? 99 : i; };
  const hit = (a: DOMRect, b: DOMRect) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

  // ---- loop ----
  let visible = true, raf = 0, last = 0;
  function loop(now: number) {
    raf = 0;
    if (!visible || document.hidden) { last = 0; return; }
    const dt = last ? Math.min(0.05, (now - last) / 1000) : 1 / 60;
    last = now;
    frame(dt);
    raf = requestAnimationFrame(loop);
  }
  function kick() { if (!poster && !raf && visible && !document.hidden) raf = requestAnimationFrame(loop); }

  canvas.classList.add('ready');
  if (poster) {
    for (let i = 0; i < 3; i++) frame(0);
    let k = 0;
    const tick = () => { frame(0); if (++k < 6) requestAnimationFrame(tick); else (window as any).__globeReady = true; };
    requestAnimationFrame(tick);
    return { setStep() {} };
  }

  new IntersectionObserver(([e]) => { visible = e.isIntersecting; kick(); }).observe(stage);
  document.addEventListener('visibilitychange', kick);

  // Drag to rotate (horizontal on touch, both axes with a mouse); scroll stays native via touch-action: pan-y.
  let drag: { x: number; y: number; id: number } | null = null;
  canvas.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY, id: e.pointerId }; canvas.setPointerCapture(e.pointerId); stage.classList.add('dragging'); });
  const end = () => { drag = null; stage.classList.remove('dragging'); };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const f = (cam.dist - 1) * 0.11;
    user.lon -= (e.clientX - drag.x) * f;
    if (e.pointerType === 'mouse') user.lat = Math.max(-40, Math.min(40, user.lat + (e.clientY - drag.y) * f));
    drag.x = e.clientX; drag.y = e.clientY;
    kick();
  });

  kick();
  return {
    setStep(i: number) {
      if (i === step) return;
      step = Math.max(0, Math.min(3, i));
      stepAt = time;
      user.lon = 0; user.lat = 0;
      kick();
    },
  };
}

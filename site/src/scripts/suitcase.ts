// Procedural orange suitcase (three.js r187). Our own geometry: no model download, no licence question.
// Loaded only when the hero is visible and the device is capable; the AVIF poster stays otherwise.
import {
  WebGLRenderer, Scene, PerspectiveCamera, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, MeshBasicMaterial,
  CylinderGeometry, TorusGeometry, BoxGeometry, CircleGeometry, PlaneGeometry, CanvasTexture, HemisphereLight,
  DirectionalLight, AmbientLight, SRGBColorSpace, Color, ACESFilmicToneMapping,
} from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

type Opts = { poster?: boolean };

function stickerTexture(text: string, bg: string, fg: string, font: string) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d')!;
  g.fillStyle = bg;
  g.beginPath();
  g.arc(128, 128, 124, 0, Math.PI * 2);
  g.fill();
  g.lineWidth = 8;
  g.strokeStyle = 'rgba(255,255,255,0.9)';
  g.stroke();
  g.fillStyle = fg;
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.direction = 'rtl';
  g.font = font;
  g.fillText(text, 128, 132);
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

function pinTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d')!;
  g.fillStyle = '#0B1F33';
  g.beginPath();
  g.roundRect(8, 8, 240, 240, 56);
  g.fill();
  g.translate(128, 132);
  g.scale(1.1, 1.1);
  g.translate(-60, -96);
  g.strokeStyle = '#FF7A00';
  g.lineWidth = 14;
  g.lineCap = 'round';
  g.stroke(new Path2D('M36 28c0-14 48-14 48 0'));
  g.fillStyle = '#FF7A00';
  g.fill(new Path2D('M60 34c-28 0-48 22-48 48 0 30 32 64 48 82 16-18 48-52 48-82 0-26-20-48-48-48zm0 30a18 18 0 1 1 0 36 18 18 0 0 1 0-36z'), 'evenodd');
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  return t;
}

function shadowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d')!;
  const r = g.createRadialGradient(64, 64, 4, 64, 64, 62);
  r.addColorStop(0, 'rgba(24,33,43,0.38)');
  r.addColorStop(1, 'rgba(24,33,43,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, 128, 128);
  return new CanvasTexture(c);
}

export async function mountSuitcase(canvas: HTMLCanvasElement, opts: Opts = {}) {
  try { await document.fonts.load('700 40px Vazirmatn', 'تورنتو'); } catch {}
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'low-power', preserveDrawingBuffer: !!opts.poster });
  renderer.setPixelRatio(opts.poster ? 2 : Math.min(1.5, window.devicePixelRatio || 1));
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  const scene = new Scene();
  const camera = new PerspectiveCamera(30, 1, 0.1, 50);
  camera.position.set(0, 0.35, 7.4);

  scene.add(new HemisphereLight(new Color('#fff6ee'), new Color('#e8d9cc'), 1.5));
  scene.add(new AmbientLight(0xffffff, 0.35));
  const key = new DirectionalLight(0xffffff, 2.4);
  key.position.set(-3, 4, 5);
  scene.add(key);
  const rim = new DirectionalLight(0xffd9c2, 1.2);
  rim.position.set(4, 2, -3);
  scene.add(rim);

  const bag = new Group();
  const shellMat = new MeshPhysicalMaterial({ color: '#c84c14', roughness: 0.42, clearcoat: 1, clearcoatRoughness: 0.18, sheen: 0.3 });
  const darkMat = new MeshStandardMaterial({ color: '#2a2f36', roughness: 0.55, metalness: 0.2 });
  const metalMat = new MeshStandardMaterial({ color: '#c9ccd1', roughness: 0.25, metalness: 0.85 });

  const W = 1.55, H = 2.15, D = 0.78;
  bag.add(new Mesh(new RoundedBoxGeometry(W, H, D, 6, 0.2), shellMat));
  // Ribs on the front and back
  for (const z of [D / 2 + 0.005, -D / 2 - 0.005]) {
    for (const x of [-0.42, -0.14, 0.14, 0.42]) {
      const rib = new Mesh(new RoundedBoxGeometry(0.11, H - 0.42, 0.06, 3, 0.03), shellMat);
      rib.position.set(x, 0, z);
      bag.add(rib);
    }
  }
  // Seam band
  const seam = new Mesh(new RoundedBoxGeometry(W + 0.02, H + 0.02, 0.06, 4, 0.03), darkMat);
  bag.add(seam);
  // Telescopic handle
  for (const x of [-0.32, 0.32]) {
    const rod = new Mesh(new CylinderGeometry(0.035, 0.035, 0.75, 12), metalMat);
    rod.position.set(x, H / 2 + 0.36, -0.18);
    bag.add(rod);
  }
  const bar = new Mesh(new RoundedBoxGeometry(0.82, 0.12, 0.14, 3, 0.05), darkMat);
  bar.position.set(0, H / 2 + 0.76, -0.18);
  bag.add(bar);
  // Carry handle on the side
  const carry = new Mesh(new TorusGeometry(0.2, 0.045, 10, 24, Math.PI), darkMat);
  carry.rotation.set(0, Math.PI / 2, Math.PI / 2);
  carry.position.set(W / 2 + 0.02, 0.35, 0);
  bag.add(carry);
  // Wheels
  for (const x of [-W / 2 + 0.2, W / 2 - 0.2]) for (const z of [-D / 2 + 0.14, D / 2 - 0.14]) {
    const w = new Mesh(new CylinderGeometry(0.11, 0.11, 0.09, 18), darkMat);
    w.rotation.z = Math.PI / 2;
    w.position.set(x, -H / 2 - 0.08, z);
    bag.add(w);
  }
  // Stickers
  const font = '800 54px Vazirmatn, Tahoma, sans-serif';
  const stickers: [string, string, string, number, number, number, number][] = [
    ['تورنتو', '#FBEEE7', '#8F320A', -0.34, 0.42, 0.4, -0.18],
    ['برلین', '#0B1F33', '#FFFFFF', 0.33, -0.22, 0.36, 0.22],
    ['مونترال', '#FFFFFF', '#0B1F33', -0.3, -0.66, 0.34, 0.1],
  ];
  stickers.forEach(([txt, bg, fg, x, y, s, rot], i) => {
    const m = new Mesh(new CircleGeometry(s, 48), new MeshBasicMaterial({ map: stickerTexture(txt, bg, fg, font), transparent: true }));
    m.position.set(x, y, D / 2 + 0.05 + i * 0.008);
    m.rotation.z = rot;
    bag.add(m);
  });
  const pin = new Mesh(new PlaneGeometry(0.46, 0.46), new MeshBasicMaterial({ map: pinTexture(), transparent: true }));
  pin.position.set(0.36, 0.62, D / 2 + 0.075);
  pin.rotation.z = -0.12;
  bag.add(pin);

  bag.rotation.set(0.08, -0.5, 0.04);
  scene.add(bag);

  const shadow = new Mesh(new PlaneGeometry(3.2, 1.3), new MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -H / 2 - 0.2;
  scene.add(shadow);

  const resize = () => {
    const r = canvas.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / Math.max(1, r.height);
    camera.updateProjectionMatrix();
  };
  resize();

  if (opts.poster) {
    renderer.render(scene, camera);
    (window as any).__suitcaseReady = true;
    return;
  }

  let px = 0, py = 0, visible = true, raf = 0;
  addEventListener('pointermove', (e) => { px = e.clientX / innerWidth - 0.5; py = e.clientY / innerHeight - 0.5; }, { passive: true });
  addEventListener('resize', resize);
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && !raf) loop(performance.now()); }).observe(canvas);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && visible && !raf) loop(performance.now()); });

  const t0 = performance.now();
  function loop(now: number) {
    raf = 0;
    if (!visible || document.hidden) return;
    const t = (now - t0) / 1000;
    bag.rotation.y += ((-0.5 + Math.sin(t * 0.45) * 0.35 + px * 0.5) - bag.rotation.y) * 0.05;
    bag.rotation.x += ((0.08 + py * 0.18) - bag.rotation.x) * 0.05;
    bag.position.y = Math.sin(t * 1.1) * 0.06;
    shadow.scale.setScalar(1 - Math.sin(t * 1.1) * 0.04);
    renderer.render(scene, camera);
    raf = requestAnimationFrame(loop);
  }
  renderer.render(scene, camera);
  canvas.classList.add('ready');
  loop(performance.now());
}

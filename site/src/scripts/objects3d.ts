// Procedural 3D objects in the hero suitcase's style (three.js r187): a house with a key, a parcel with a plane.
// Build-only: scripts/posters.mjs bundles this file, renders each scene once and saves an AVIF. Never shipped to visitors.
import {
  WebGLRenderer, Scene, PerspectiveCamera, Group, Mesh, MeshPhysicalMaterial, MeshStandardMaterial, MeshBasicMaterial,
  CylinderGeometry, TorusGeometry, BoxGeometry, PlaneGeometry, SphereGeometry, CapsuleGeometry, ExtrudeGeometry, Shape,
  CanvasTexture, HemisphereLight, DirectionalLight, AmbientLight, SRGBColorSpace, Color, ACESFilmicToneMapping, Object3D,
} from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

export type SceneName = 'house' | 'parcel';

const ORANGE = '#c84c14';
const ORANGE_DEEP = '#a63d0f';
const NAVY = '#0b1f33';
const CREAM = '#fbeee7';

function shadowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d')!;
  const r = g.createRadialGradient(64, 64, 4, 64, 64, 62);
  r.addColorStop(0, 'rgba(24,33,43,0.36)');
  r.addColorStop(1, 'rgba(24,33,43,0)');
  g.fillStyle = r;
  g.fillRect(0, 0, 128, 128);
  return new CanvasTexture(c);
}

function labelTexture() {
  const c = document.createElement('canvas');
  c.width = 256;
  c.height = 176;
  const g = c.getContext('2d')!;
  g.fillStyle = '#ffffff';
  g.beginPath();
  g.roundRect(4, 4, 248, 168, 22);
  g.fill();
  g.fillStyle = NAVY;
  g.beginPath();
  g.roundRect(24, 28, 46, 46, 12);
  g.fill();
  g.fillStyle = '#FF7A00';
  g.fill(new Path2D('M47 36c-8 0-14 6-14 14 0 9 9 18 14 23 5-5 14-14 14-23 0-8-6-14-14-14zm0 9a5 5 0 1 1 0 10 5 5 0 0 1 0-10z'), 'evenodd');
  g.fillStyle = '#c9d2dc';
  for (const [x, y, w] of [[88, 36, 130], [88, 62, 96], [24, 100, 194], [24, 126, 150]] as const) {
    g.beginPath();
    g.roundRect(x, y, w, 12, 6);
    g.fill();
  }
  const t = new CanvasTexture(c);
  t.colorSpace = SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

const shell = () => new MeshPhysicalMaterial({ color: ORANGE, roughness: 0.42, clearcoat: 1, clearcoatRoughness: 0.18, sheen: 0.3 });
const navyMat = () => new MeshPhysicalMaterial({ color: NAVY, roughness: 0.38, clearcoat: 0.8, clearcoatRoughness: 0.2 });
const darkMat = () => new MeshStandardMaterial({ color: '#2a2f36', roughness: 0.55, metalness: 0.2 });
const brass = () => new MeshStandardMaterial({ color: '#d9a441', roughness: 0.28, metalness: 0.9 });

function at<T extends Object3D>(o: T, x: number, y: number, z: number): T {
  o.position.set(x, y, z);
  return o;
}

// A small house: orange walls, deeper orange gable roof, navy door and windows, brass key leaning in front.
function house(): Group {
  const g = new Group();
  const W = 1.9, H = 1.35, D = 1.6;
  const walls = shell();
  g.add(at(new Mesh(new RoundedBoxGeometry(W, H, D, 5, 0.1), walls), 0, H / 2, 0));
  // Gable roof: a rounded triangle prism, overhanging the walls.
  const rw = W / 2 + 0.22, rh = 0.95;
  const s = new Shape();
  s.moveTo(-rw, 0);
  s.lineTo(0, rh);
  s.lineTo(rw, 0);
  s.lineTo(-rw, 0);
  const roofGeo = new ExtrudeGeometry(s, { depth: D + 0.3, bevelEnabled: true, bevelThickness: 0.06, bevelSize: 0.06, bevelSegments: 4, curveSegments: 4 });
  roofGeo.translate(0, 0, -(D + 0.3) / 2);
  const roofMat = new MeshPhysicalMaterial({ color: ORANGE_DEEP, roughness: 0.4, clearcoat: 1, clearcoatRoughness: 0.2 });
  g.add(at(new Mesh(roofGeo, roofMat), 0, H - 0.02, 0));
  // Round attic window on the gable.
  g.add(at(new Mesh(new TorusGeometry(0.15, 0.035, 10, 32), navyMat()), 0, H + 0.32, D / 2 + 0.19));
  g.add(at(new Mesh(new CylinderGeometry(0.13, 0.13, 0.02, 32).rotateX(Math.PI / 2), new MeshStandardMaterial({ color: '#9fb4c8', roughness: 0.15, metalness: 0.4 })), 0, H + 0.32, D / 2 + 0.18));
  // Chimney.
  g.add(at(new Mesh(new RoundedBoxGeometry(0.26, 0.55, 0.26, 3, 0.05), navyMat()), 0.55, H + 0.62, -0.3));
  // Door, frame and knob.
  const door = new Mesh(new RoundedBoxGeometry(0.5, 0.82, 0.08, 4, 0.05), navyMat());
  g.add(at(door, 0, 0.43, D / 2 + 0.02));
  g.add(at(new Mesh(new SphereGeometry(0.04, 16, 12), brass()), 0.15, 0.42, D / 2 + 0.08));
  g.add(at(new Mesh(new RoundedBoxGeometry(0.62, 0.06, 0.16, 2, 0.02), new MeshStandardMaterial({ color: CREAM, roughness: 0.6 })), 0, 0.03, D / 2 + 0.08));
  // Front windows with cream frames and navy glass.
  for (const x of [-0.6, 0.6]) {
    g.add(at(new Mesh(new RoundedBoxGeometry(0.42, 0.42, 0.06, 3, 0.04), new MeshStandardMaterial({ color: CREAM, roughness: 0.6 })), x, 0.78, D / 2 + 0.02));
    g.add(at(new Mesh(new RoundedBoxGeometry(0.32, 0.32, 0.06, 3, 0.03), new MeshPhysicalMaterial({ color: '#1d3a5c', roughness: 0.12, clearcoat: 1 })), x, 0.78, D / 2 + 0.04));
  }
  // Side window.
  g.add(at(new Mesh(new RoundedBoxGeometry(0.06, 0.42, 0.42, 3, 0.04), new MeshStandardMaterial({ color: CREAM, roughness: 0.6 })), W / 2 + 0.02, 0.78, 0));
  g.add(at(new Mesh(new RoundedBoxGeometry(0.06, 0.32, 0.32, 3, 0.03), new MeshPhysicalMaterial({ color: '#1d3a5c', roughness: 0.12, clearcoat: 1 })), W / 2 + 0.04, 0.78, 0));

  // Key, leaning against the front wall: ring, shaft, teeth.
  const key = new Group();
  const m = brass();
  key.add(new Mesh(new TorusGeometry(0.2, 0.06, 14, 36), m));
  const shaft = new Mesh(new CylinderGeometry(0.045, 0.045, 0.78, 16), m);
  shaft.rotation.z = Math.PI / 2;
  key.add(at(shaft, 0.58, 0, 0));
  key.add(at(new Mesh(new RoundedBoxGeometry(0.09, 0.16, 0.06, 2, 0.02), m), 0.86, -0.1, 0));
  key.add(at(new Mesh(new RoundedBoxGeometry(0.09, 0.11, 0.06, 2, 0.02), m), 0.7, -0.08, 0));
  key.add(at(new Mesh(new RoundedBoxGeometry(0.06, 0.12, 0.08, 2, 0.02), m), 0.32, 0, 0));
  key.rotation.set(-0.35, 0.25, 0.95);
  key.scale.setScalar(0.95);
  g.add(at(key, 1.02, 0.3, D / 2 + 0.6));
  return g;
}

// A taped parcel with a label and a small navy plane circling it on a dotted orbit.
function parcel(): { root: Group; plane: Group } {
  const g = new Group();
  const S = 1.5, Hh = 1.2;
  const card = new MeshPhysicalMaterial({ color: '#d99a5b', roughness: 0.62, clearcoat: 0.25, clearcoatRoughness: 0.5 });
  g.add(at(new Mesh(new RoundedBoxGeometry(S, Hh, S, 5, 0.07), card), 0, Hh / 2, 0));
  // Lid seam and orange tape across the top and down the front and back.
  const tape = shell();
  g.add(at(new Mesh(new BoxGeometry(0.34, 0.02, S + 0.02), tape), 0, Hh + 0.005, 0));
  g.add(at(new Mesh(new BoxGeometry(0.34, 0.42, 0.02), tape), 0, Hh - 0.2, S / 2 + 0.005));
  g.add(at(new Mesh(new BoxGeometry(0.34, 0.42, 0.02), tape), 0, Hh - 0.2, -S / 2 - 0.005));
  // Shipping label on the front.
  const label = new Mesh(new PlaneGeometry(0.62, 0.43), new MeshBasicMaterial({ map: labelTexture(), transparent: true }));
  g.add(at(label, -0.3, 0.68, S / 2 + 0.015)).rotation.z = 0.04;
  // Side "this way up" mark: two small navy arrows.
  for (const z of [-0.2, 0.2]) {
    const a = new Mesh(new CylinderGeometry(0, 0.08, 0.14, 3), navyMat());
    g.add(at(a, S / 2 + 0.01, 0.82, z)).rotation.set(0, 0, -Math.PI / 2 + Math.PI / 2);
    g.add(at(new Mesh(new BoxGeometry(0.02, 0.18, 0.04), navyMat()), S / 2 + 0.01, 0.66, z));
  }

  // Plane: capsule fuselage, swept wings, tail plane and fin, orange nose.
  const plane = new Group();
  const n = navyMat();
  const body = new Mesh(new CapsuleGeometry(0.1, 0.56, 8, 20), n);
  body.rotation.z = Math.PI / 2;
  plane.add(body);
  const swept = (root: number, span: number, sweep: number, tip: number) => {
    const w = new Shape();
    w.moveTo(root / 2, 0);
    w.lineTo(root / 2 - sweep, span);
    w.lineTo(root / 2 - sweep - tip, span);
    w.lineTo(-root / 2, 0);
    w.lineTo(root / 2 - sweep - tip, -span);
    w.lineTo(root / 2 - sweep, -span);
    w.lineTo(root / 2, 0);
    const geo = new ExtrudeGeometry(w, { depth: 0.02, bevelEnabled: true, bevelThickness: 0.012, bevelSize: 0.012, bevelSegments: 2 });
    geo.rotateX(Math.PI / 2);
    return new Mesh(geo, n);
  };
  plane.add(at(swept(0.3, 0.56, 0.2, 0.12), 0.06, 0.0, 0));
  plane.add(at(swept(0.14, 0.22, 0.08, 0.07), -0.32, 0.04, 0));
  const fs = new Shape();
  fs.moveTo(0, 0);
  fs.lineTo(-0.12, 0.22);
  fs.lineTo(-0.2, 0.22);
  fs.lineTo(-0.16, 0);
  fs.lineTo(0, 0);
  plane.add(at(new Mesh(new ExtrudeGeometry(fs, { depth: 0.02, bevelEnabled: true, bevelThickness: 0.01, bevelSize: 0.01, bevelSegments: 2 }), n), -0.22, 0.04, -0.01));
  plane.add(at(new Mesh(new SphereGeometry(0.1, 20, 14, 0, Math.PI * 2, 0, Math.PI / 2).rotateZ(-Math.PI / 2), shell()), 0.3, 0, 0));
  const win = new MeshStandardMaterial({ color: '#e3e9f0', roughness: 0.3 });
  for (let i = 0; i < 4; i++) for (const z of [-0.088, 0.088]) plane.add(at(new Mesh(new SphereGeometry(0.018, 10, 8), win), 0.16 - i * 0.09, 0.035, z));

  // Orbit: dotted ring of small orange beads around the box, tilted towards the camera.
  const orbit = new Group();
  const bead = new SphereGeometry(0.026, 10, 8);
  const beadMat = new MeshStandardMaterial({ color: '#e07a3f', roughness: 0.4 });
  const R = 1.6, pa = -0.75;
  for (let i = 0; i < 54; i++) {
    const a = (i / 54) * Math.PI * 2 - Math.PI;
    if (Math.abs(a - pa) < 0.34) continue; // gap where the plane flies
    orbit.add(at(new Mesh(bead, beadMat), Math.cos(a) * R, 0, Math.sin(a) * R));
  }
  orbit.add(at(plane, Math.cos(pa) * R, 0.02, Math.sin(pa) * R));
  plane.rotation.set(0, -pa - Math.PI / 2, 0);
  plane.rotateX(-0.3);
  plane.scale.setScalar(1.15);
  orbit.rotation.set(0.32, 0, 0.36);
  orbit.position.y = Hh * 0.6;
  const root = new Group();
  g.rotation.set(0.06, -0.62, 0);
  root.add(g, orbit);
  return { root, plane };
}

export async function renderObject(canvas: HTMLCanvasElement, name: SceneName) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(2);
  renderer.outputColorSpace = SRGBColorSpace;
  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  const scene = new Scene();
  const camera = new PerspectiveCamera(30, 1, 0.1, 50);

  scene.add(new HemisphereLight(new Color('#fff6ee'), new Color('#e8d9cc'), 1.5));
  scene.add(new AmbientLight(0xffffff, 0.35));
  const key = new DirectionalLight(0xffffff, 2.4);
  key.position.set(-3, 4, 5);
  scene.add(key);
  const rim = new DirectionalLight(0xffd9c2, 1.2);
  rim.position.set(4, 2, -3);
  scene.add(rim);

  let obj: Group;
  if (name === 'house') {
    obj = house();
    obj.rotation.set(0.05, -0.55, 0);
    obj.position.set(0, -1.05, 0);
    camera.position.set(0, 1.0, 7.6);
    camera.lookAt(0, 0.05, 0);
  } else {
    obj = parcel().root;
    obj.position.set(0, -0.62, 0);
    camera.position.set(0, 1.25, 7.6);
    camera.lookAt(0, 0.05, 0);
  }
  scene.add(obj);

  const shadow = new Mesh(new PlaneGeometry(3.6, 2.2), new MeshBasicMaterial({ map: shadowTexture(), transparent: true, depthWrite: false }));
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = obj.position.y - 0.01;
  scene.add(shadow);

  const r = canvas.getBoundingClientRect();
  renderer.setSize(r.width, r.height, false);
  camera.aspect = r.width / Math.max(1, r.height);
  camera.updateProjectionMatrix();
  renderer.render(scene, camera);
  (window as any).__objReady = true;
}

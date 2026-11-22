// Build the courier globe's geo file from world-atlas (ISC, Natural Earth 1:110m). Run manually, commit the output.
//   node scripts/geo.mjs  → public/media/site/geo/world-110m.bin
// Format (little parsing, no runtime dependency): coordinates quantized to 0.02° on an equirectangular grid
// (x = (lon + 180) / 0.02, y = (90 - lat) / 0.02), each ring delta + zigzag + LEB128 varint encoded.
//   u8 layerCount, then per layer: varint ringCount, per ring: varint pointCount, then pointCount × (dx, dy) varints.
// Longitudes are unwrapped (may leave ±180) so rings crossing the antimeridian stay continuous: the client draws a ring
// again at ±360° when it spills over. A ring that wraps the whole globe (Antarctica) is closed through its pole.
// Layers: 0 land polygons, 1 country borders (lines), 2 Iran, 3 Canada, 4 Germany (polygons).
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { feature, mesh } from 'topojson-client';

const require = createRequire(import.meta.url);
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const topo = JSON.parse(readFileSync(require.resolve('world-atlas/countries-110m.json'), 'utf8'));
const Q = 0.02;

const rings = (geom) => {
  if (!geom) return [];
  if (geom.type === 'Polygon') return geom.coordinates;
  if (geom.type === 'MultiPolygon') return geom.coordinates.flat();
  if (geom.type === 'LineString') return [geom.coordinates];
  if (geom.type === 'MultiLineString') return geom.coordinates;
  if (geom.type === 'FeatureCollection') return geom.features.flatMap((f) => rings(f.geometry));
  if (geom.type === 'Feature') return rings(geom.geometry);
  return [];
};
const country = (id) => feature(topo, topo.objects.countries.geometries.find((g) => g.id === id));

const unwrap = (ring, closed) => {
  const out = [];
  let k = 0;
  for (const [lon, lat] of ring) {
    if (out.length) {
      const prev = out[out.length - 1][0];
      while (lon + k - prev > 180) k -= 360;
      while (lon + k - prev < -180) k += 360;
    }
    out.push([lon + k, lat]);
  }
  const first = out[0], last = out[out.length - 1];
  if (closed && Math.abs(last[0] - first[0]) > 180) {
    const pole = out.reduce((s, c) => s + c[1], 0) < 0 ? -90 : 90;
    out.push([last[0], pole], [first[0], pole]);
  }
  return out;
};
const poly = (g) => rings(g).map((r) => unwrap(r, true));
const layers = [
  poly(feature(topo, topo.objects.land)),
  rings(mesh(topo, topo.objects.countries, (a, b) => a !== b)).map((r) => unwrap(r, false)),
  poly(country('364')),
  poly(country('124')),
  poly(country('276')),
];

const bytes = [];
const varint = (n) => { do { let b = n & 0x7f; n >>>= 7; if (n) b |= 0x80; bytes.push(b); } while (n); };
const zig = (n) => (n << 1) ^ (n >> 31);
bytes.push(layers.length);
let points = 0;
for (const layer of layers) {
  const qs = layer.map((ring) => {
    const out = [];
    for (const [lon, lat] of ring) {
      const x = Math.round((lon + 180) / Q), y = Math.round((90 - lat) / Q);
      const last = out[out.length - 1];
      if (!last || last[0] !== x || last[1] !== y) out.push([x, y]);
    }
    return out;
  }).filter((r) => r.length > 1);
  varint(qs.length);
  for (const r of qs) {
    varint(r.length);
    points += r.length;
    let px = 0, py = 0;
    for (const [x, y] of r) { varint(zig(x - px)); varint(zig(y - py)); px = x; py = y; }
  }
}
const buf = Buffer.from(bytes);
const dir = resolve(root, 'public/media/site/geo');
mkdirSync(dir, { recursive: true });
writeFileSync(resolve(dir, 'world-110m.bin'), buf);
console.log(`world-110m.bin: ${points} points, ${buf.length} B raw, ${gzipSync(buf, { level: 9 }).length} B gzip`);

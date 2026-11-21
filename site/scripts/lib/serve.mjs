// Minimal static server for dist/ (used by the poster, OG and screenshot scripts; no extra dependency).
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { extname, join, resolve } from 'node:path';

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.avif': 'image/avif', '.webp': 'image/webp',
  '.woff2': 'font/woff2', '.xml': 'application/xml', '.txt': 'text/plain; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
  '.mp4': 'video/mp4', '.vtt': 'text/vtt', '.mp3': 'audio/mpeg', '.csv': 'text/csv; charset=utf-8', '.ico': 'image/x-icon',
};

export function serve(dir, port = 4329, extraRoots = {}) {
  const root = resolve(dir);
  const server = createServer(async (req, res) => {
    let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    let base = root;
    for (const [prefix, d] of Object.entries(extraRoots)) {
      if (p.startsWith(prefix)) { base = resolve(d); p = p.slice(prefix.length - 1); break; }
    }
    let file = join(base, p);
    try {
      const s = await stat(file);
      if (s.isDirectory()) file = join(file, 'index.html');
      const body = await readFile(file);
      res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' });
      res.end(body);
    } catch {
      try {
        res.writeHead(404, { 'content-type': 'text/html; charset=utf-8' });
        res.end(await readFile(join(root, '404.html')));
      } catch { res.end('404'); }
    }
  });
  return new Promise((ok) => server.listen(port, () => ok(server)));
}

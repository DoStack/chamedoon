// Serves the build the way Django and the Vercel CDN do: /static/spa/* as files, /app/v2/* as the shell.
import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { dirname, extname, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const spa = resolve(dirname(fileURLToPath(import.meta.url)), '../../backend/spa')
const TYPES = { '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' }

createServer(async (req, res) => {
  const { pathname } = new URL(req.url, 'http://local')
  try {
    if (pathname.startsWith('/app/v2/')) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
      res.end(await readFile(resolve(spa, 'shell/index.html')))
    } else if (pathname.startsWith('/static/spa/')) {
      const file = resolve(spa, 'static/spa', normalize(pathname.slice('/static/spa/'.length)))
      const body = await readFile(file)
      res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' })
      res.end(body)
    } else {
      res.writeHead(404).end()
    }
  } catch {
    res.writeHead(404).end()
  }
}).listen(Number(process.argv[2]), '127.0.0.1')

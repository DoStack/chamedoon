// Generates TS types from the backend contract. The output is gitignored, so
// `npm run typecheck` always checks the frontend against the current contract.
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const spec = resolve(root, '../backend/home_api/openapi.yaml')
const out = resolve(root, 'src/api/schema.gen.ts')

if (!existsSync(spec)) {
  console.log('gen:api skipped: backend/home_api/openapi.yaml does not exist yet')
  process.exit(0)
}

mkdirSync(dirname(out), { recursive: true })
// Relative paths: the checkout path can hold spaces, and this goes through a shell.
const args = ['openapi-typescript', relative(root, spec), '-o', relative(root, out)]
const result = spawnSync('npx', args, { stdio: 'inherit', cwd: root, shell: true })
process.exit(result.status ?? 1)

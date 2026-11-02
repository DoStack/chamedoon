import { defineConfig } from '@playwright/test'

const PORT = 4179

export default defineConfig({
  testDir: './e2e',
  forbidOnly: Boolean(process.env.CI),
  reporter: 'list',
  outputDir: 'test-results',
  use: { baseURL: `http://127.0.0.1:${PORT}`, screenshot: 'only-on-failure' },
  webServer: {
    command: `node e2e/serve.mjs ${PORT}`,
    url: `http://127.0.0.1:${PORT}/app/v2/`,
    reuseExistingServer: !process.env.CI,
  },
})

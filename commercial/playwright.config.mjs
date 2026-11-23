import { randomUUID } from 'node:crypto'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { defineConfig } from '@playwright/test'

const baseURL = 'http://127.0.0.1:8792'
process.env.ASC_BROWSER_DB ??= join(tmpdir(), `asc-browser-${randomUUID()}.sqlite`)

export default defineConfig({
  testDir: './browser-tests',
  reporter: 'list',
  workers: 3,
  // Nine browser projects share one machine; allow headroom for slower runs.
  timeout: 60_000,
  use: { baseURL, reducedMotion: 'reduce' },
  webServer: {
    command: 'node server/index.js',
    url: baseURL,
    // The waitlist specs need it open; outside production the legal lock can be lifted for tests only
    env: { COMMERCIAL_PORT: '8792', COMMERCIAL_DB: process.env.ASC_BROWSER_DB, COMMERCIAL_WAITLIST_OPEN: 'true', COMMERCIAL_LEGAL_TEST_PUBLISHED: 'true' },
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'firefox', use: { browserName: 'firefox' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
})

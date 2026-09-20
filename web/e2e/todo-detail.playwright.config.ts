import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  fullyParallel: false,
  timeout: 30000,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8010',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 8010 --strictPort',
    port: 8010,
    reuseExistingServer: true,
  },
})

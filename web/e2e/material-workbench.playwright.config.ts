import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: '.',
  testMatch: 'material-workbench.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  use: {
    trace: 'on-first-retry',
    launchOptions: process.env.PLAYWRIGHT_CHROME_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROME_EXECUTABLE }
      : undefined,
  },
})

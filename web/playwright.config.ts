import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  timeout: 30000,
  // Wave 5: 预先登录 admin 一次，共享 storageState 到所有 spec
  globalSetup: './e2e/helpers/global-setup.ts',
  // 截图密集；workers>2 会偶发 "Unable to capture screenshot"
  workers: process.env.CI ? 2 : 2,
  use: {
    baseURL: 'http://127.0.0.1:8010',
    storageState: './e2e/.auth/admin.json',
    trace: 'on-first-retry',
  },
  webServer: [
    {
      command: 'PYTHONPATH=.. python3 ../scripts/run_e2e_backend.py',
      port: 8000,
      reuseExistingServer: true,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 8010 --strictPort',
      port: 8010,
      reuseExistingServer: true,
    },
  ],
})

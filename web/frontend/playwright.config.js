import { defineConfig, devices } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDirectory = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(frontendDirectory, '..', '..')
const python = process.env.NOVELSIM_PYTHON || (
  process.platform === 'win32'
    ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
    : 'python'
)
const e2eServer = path.join(projectRoot, 'tests', 'e2e', 'serve.py')

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:8876',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: `"${python}" "${e2eServer}"`,
    url: 'http://127.0.0.1:8876/api/meta/contract',
    timeout: 120_000,
    reuseExistingServer: false,
  },
})

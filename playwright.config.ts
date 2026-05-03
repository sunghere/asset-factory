import { defineConfig, devices } from '@playwright/test';

/**
 * Asset Factory — Playwright e2e config.
 *
 * - chromium 단일 (모든 critical user flow 가 chromium 에서 검증되면 충분).
 * - baseURL = http://localhost:8000 (run-dev.sh start 가 띄우는 포트).
 * - webServer 가 자동 기동: 이미 떠 있으면 재사용 (`reuseExistingServer`).
 * - testDir = tests/e2e — Python pytest 와 디렉토리 격리.
 *
 * Pytest 와 동시 실행될 때 DB 충돌 방지를 위해 ASSET_FACTORY_DB_PATH /
 * ASSET_FACTORY_DATA_DIR 을 e2e 전용 임시 경로로 띄운다 (Python 단위 테스트는
 * tmp_path fixture 라 격리 OK).
 */
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,  // 단일 dev server 공유. parallel 시 race risk.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,  // 같은 백엔드 DB 공유 — serial 강제.
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://localhost:8000',
    trace: 'on-first-retry',
    actionTimeout: 8000,
    navigationTimeout: 15000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: './run-dev.sh start',
    url: 'http://localhost:8000/api/health',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});

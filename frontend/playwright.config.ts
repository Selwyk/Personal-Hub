import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './e2e', workers: 1, retries: 0, timeout: 120000,
  use: { baseURL: process.env.HUB_E2E_URL || 'http://localhost:15173', browserName: 'chromium', viewport: { width: 1440, height: 1000 }, screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  reporter: [['list']], outputDir: 'test-results' });

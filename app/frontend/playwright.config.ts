import { defineConfig } from '@playwright/test';

const ui = 'http://127.0.0.1:5173';
const backend = '../backend';
// Fixed credentials, valid only for this throwaway sim process on localhost.
const sim = 'customer=e2e-customer-token,sales=e2e-sales-token,test-admin=e2e-admin-token';

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: ui, headless: true, trace: 'retain-on-failure' },
  webServer: [
    {
      // Simulated MCP systems for the Sales agent's inventory (sales token only reaches inventory).
      command: `uv run --frozen --project ${backend} acme-sim`,
      url: 'http://127.0.0.1:8700/mcp/inventory/',
      env: { ACME_SIM_TOKENS: sim, ACME_SIM_PORT: '8700' },
      reuseExistingServer: !process.env.CI,
      stdout: 'ignore',
      stderr: 'pipe',
      // Playwright treats 401/403 as 'up': a bare GET without a token is refused.
    },
    {
      command: `uv run --frozen --project ${backend} python e2e/serve_customer.py`,
      url: 'http://127.0.0.1:8000/journeys',
      env: { LLM_MODE: 'fake', E2E_CUSTOMER_PORT: '8000' },
      reuseExistingServer: !process.env.CI,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      command: `uv run --frozen --project ${backend} acme-sales`,
      url: 'http://127.0.0.1:8801/.well-known/agent-card.json',
      env: {
        ACME_SALES_TOKEN: 'e2e-sales-token',
        ACME_CUSTOMER_URL: 'http://127.0.0.1:8000',
        ACME_SIM_URL: 'http://127.0.0.1:8700',
        ACME_SALES_TICK_SECONDS: '1',
      },
      reuseExistingServer: !process.env.CI,
      stdout: 'ignore',
      stderr: 'pipe',
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort',
      url: ui,
      reuseExistingServer: !process.env.CI,
      stdout: 'ignore',
      stderr: 'pipe',
    },
  ],
});

import { expect, test, type Page } from '@playwright/test';

// Guided scenario runner (#146). Runs after narrative.spec.ts (alphabetical order) because
// starting a preset swaps the backend's clock for its own fixed one.
const API = 'http://127.0.0.1:8000';

const status = (page: Page) =>
  page.locator('ol[aria-label="Disruption status"] [aria-current="step"]');
const row = (page: Page, term: string) =>
  page.locator('dl.itinerary .row').filter({ has: page.getByText(term, { exact: true }) });
const timeline = (page: Page) => page.getByRole('log', { name: 'Agent timeline' });

async function nextStep(page: Page, n: number, total: number) {
  await page.getByRole('button', { name: 'Next step' }).click();
  await expect(page.getByText(`Step ${n} of ${total}`)).toBeVisible();
  await expect(page.getByText('Matches the expectation')).toBeVisible();
}

test.afterAll(async ({ request }) => {
  await request.post(`${API}/scenario/reset`);
});

test('happy-path preset: Next step walks AC1240 -> AC1252 -> offer -> 2A -> CLOSED', async ({
  page,
}) => {
  await page.goto('/console');
  await expect(page.getByText('Demo, all systems mocked')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next step' })).toBeDisabled();
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption('happy-path');
  await page.getByRole('button', { name: 'Start scenario' }).click();

  await nextStep(page, 1, 5);
  await expect(status(page)).toHaveText('Resolved');
  await expect(row(page, 'Flight')).toContainText('AC1240');
  await nextStep(page, 2, 5);
  await nextStep(page, 3, 5);
  await expect(row(page, 'Flight')).toContainText('AC1252');
  await nextStep(page, 4, 5);
  await expect(row(page, 'Upgrade offer')).toContainText('sent');
  await nextStep(page, 5, 5);
  await expect(status(page)).toHaveText('Closed');
  await expect(row(page, 'Seat')).toContainText('2A');
  await expect(page.getByText(/scenario finished/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next step' })).toBeDisabled();
});

test('seat-taken preset shows the nearest window seat with a matching expectation', async ({
  page,
}) => {
  await page.goto('/console');
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption('seat-taken');
  await page.getByRole('button', { name: 'Start scenario' }).click();
  await expect(page.getByText(/no journey open/i)).toBeVisible();
  await nextStep(page, 1, 2);
  await expect(row(page, 'Seat')).toContainText('15A');
  await expect(row(page, 'Flight')).toContainText('AC1240');
  await nextStep(page, 2, 2);
});

test('declines-upgrade preset: declining closes the journey on AC1252 seat 14A with all checks verified', async ({
  page,
}) => {
  await page.goto('/console');
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption('declines-upgrade');
  await page.getByRole('button', { name: 'Start scenario' }).click();
  await expect(page.getByText(/no journey open/i)).toBeVisible();

  for (let i = 1; i <= 5; i += 1) {
    await nextStep(page, i, 5);
  }
  await expect(status(page)).toHaveText('Closed');
  await expect(row(page, 'Flight')).toContainText('AC1252');
  await expect(row(page, 'Seat')).toContainText('14A');
  await expect(row(page, 'Upgrade offer')).toContainText('declined: 135.00 (standard 290.00)');
  for (const check of ['flight: ✓', 'seat: ✓', 'hotel: ✓', 'baggage: ✓', 'upgrade: ✓']) {
    await expect(row(page, 'Verification')).toContainText(check);
  }
});

test('upgrade-race preset: replanning invalidates the upgrade offer and keeps AC1252 seat 14A', async ({
  page,
}) => {
  await page.goto('/console');
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption('upgrade-race');
  await page.getByRole('button', { name: 'Start scenario' }).click();
  await expect(page.getByText(/no journey open/i)).toBeVisible();

  for (let i = 1; i <= 6; i += 1) {
    await nextStep(page, i, 6);
  }
  await expect(status(page)).toHaveText('Resolved');
  await expect(row(page, 'Flight')).toContainText('AC1252');
  await expect(row(page, 'Seat')).toContainText('14A');
  await expect(row(page, 'Upgrade offer')).toContainText('invalidated: 135.00 (standard 290.00)');
});

test('no-offer journey on AC1240 closes after the 10-minute offer window elapses', async ({
  page,
  request,
}) => {
  await request.post(`${API}/scenario/reset`);
  await page.goto('/console');
  await page.getByRole('button', { name: 'Reset' }).click();
  await expect(page.getByText(/no journey open/i)).toBeVisible();
  await page.getByRole('button', { name: 'Trigger AC1234 cancellation' }).click();
  await expect(status(page)).toHaveText('Resolved');
  await expect(row(page, 'Flight')).toContainText('AC1240');

  await request.post(`${API}/e2e/advance?minutes=6`);
  await expect(timeline(page)).toContainText('Notification sent');
  await request.post(`${API}/whatsapp/P-MARIANA/messages`, {
    data: { text: 'Perfect, thanks!' },
  });
  await expect(timeline(page)).toContainText('Resolution acknowledged');

  await request.post(`${API}/e2e/advance?minutes=11`);
  await expect(status(page)).toHaveText('Closed');
  await expect(timeline(page)).toContainText('Service closure');
  await expect(timeline(page)).toContainText('Offer window elapsed');
  await expect(timeline(page)).toContainText('Verification passed');
  for (const check of ['flight: ✓', 'seat: ✓', 'hotel: ✓', 'baggage: ✓', 'upgrade: ✓']) {
    await expect(row(page, 'Verification')).toContainText(check);
  }
});

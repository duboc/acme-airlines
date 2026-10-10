import { expect, test, type Page } from '@playwright/test';

// Mariana's AC1234 story end to end through the UI: operator console + simulated WhatsApp.
// Real Customer and Sales agents, simulated MCP systems, FakeLlm, fixed clock (see serve_customer.py).
const API = 'http://127.0.0.1:8000';

const status = (page: Page) =>
  page.locator('ol[aria-label="Disruption status"] [aria-current="step"]');
const row = (page: Page, term: string) =>
  page.locator('dl.itinerary .row').filter({ has: page.getByText(term, { exact: true }) });
const bubbles = (page: Page) => page.locator('.bubble');
const offerBubbles = (page: Page) =>
  page.locator('.bubble').filter({ hasText: 'Optional. You can ignore this.' });
const timeline = (page: Page) => page.getByRole('log', { name: 'Agent timeline' });

test('Mariana: cancellation, rebuild, replanning, guardrailed upsell, conversion, closed loop', async ({
  context,
  request,
}) => {
  const console_ = await context.newPage();
  const chat = await context.newPage();

  // Scenario 1-2: the operator triggers the cancellation; the journey is rebuilt backstage.
  await console_.goto('/console');
  await console_.getByRole('button', { name: 'Trigger AC1234 cancellation' }).click();
  await expect(status(console_)).toHaveText('Resolved');
  await expect(row(console_, 'Flight')).toContainText('AC1240');
  await expect(row(console_, 'Seat')).toContainText('14A');
  await expect(row(console_, 'Hotel')).not.toContainText('Not arranged yet');
  await expect(row(console_, 'Vouchers')).toContainText('meal');
  await expect(timeline(console_)).toContainText('Hotel booked');
  await expect(timeline(console_)).toContainText('Sales held back');

  // Scenario 3: 6 fixed-clock minutes later (10:09 PM) the one WhatsApp message arrives.
  await request.post(`${API}/e2e/advance?minutes=6`);
  await chat.goto('/whatsapp');
  await expect(bubbles(chat)).toHaveCount(1);
  const first = bubbles(chat).first();
  for (const fact of ['AC1234', 'AC1240', '14A', 'HT8842']) await expect(first).toContainText(fact);
  await expect(offerBubbles(chat)).toHaveCount(0);

  // The passenger asks for a departure after 10:00 -> AC1252 at 11:40.
  await chat.getByLabel('Message').fill('Can I leave after 10am?');
  await chat.getByRole('button', { name: 'Send' }).click();
  await expect(chat.locator('.bubble-row.outbound').last()).toContainText('AC1252');
  await expect(status(console_)).toHaveText('Resolved');
  await expect(row(console_, 'Flight')).toContainText('AC1252');
  await expect(row(console_, 'Seat')).toContainText('14A');
  await expect(
    console_.getByText('Passenger has not acknowledged the resolution yet.'),
  ).toBeVisible();

  // Guardrail: the Sales radar ticks every second; nothing may be pitched before she acknowledges
  // the FINAL (AC1252) resolution.
  await chat.waitForTimeout(4000);
  await expect(offerBubbles(chat)).toHaveCount(0);
  await expect(chat.getByRole('button', { name: 'Accept' })).toHaveCount(0);
  await expect(row(console_, 'Upgrade offer')).toContainText('None');

  // Scenario 4: she acknowledges; the optional, zero-pressure offer appears after it.
  const before = await bubbles(chat).count();
  await chat.getByLabel('Message').fill('Perfect, thanks!');
  await chat.getByRole('button', { name: 'Send' }).click();
  await expect(offerBubbles(chat)).toHaveCount(1);
  const offer = offerBubbles(chat).first();
  await expect(offer).toContainText('optional');
  await expect(offer).toContainText('$135.00');
  await expect(offer).toContainText('no pressure');
  await expect(offer.getByRole('button', { name: 'Accept' })).toBeVisible();
  await expect(offer.getByRole('button', { name: 'No thanks' })).toBeVisible();
  expect(await bubbles(chat).count()).toBeGreaterThan(before + 1); // her reply, then the offer
  const order = await bubbles(chat).allInnerTexts();
  expect(order.findIndex((t) => t.includes('Perfect, thanks!'))).toBeLessThan(
    order.findIndex((t) => t.includes('Optional. You can ignore this.')),
  );
  await expect(timeline(console_)).toContainText('Approved');

  // Scenario 5: one tap -> seat 2A confirmed, journey closed, verification passed.
  await offer.getByRole('button', { name: 'Accept' }).click();
  await expect(chat.locator('.bubble-row.outbound').last()).toContainText('2A');
  await expect(row(console_, 'Seat')).toContainText('2A');
  await expect(row(console_, 'Seat')).toContainText('premium_business');
  await expect(status(console_)).toHaveText('Closed');
  await expect(timeline(console_)).toContainText('Verification passed');
});

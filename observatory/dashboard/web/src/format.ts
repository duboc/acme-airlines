export const usd = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

export const num = (n: number) => n.toLocaleString('en-US');

export const BILLING_EMPTY = 'Billing export not enabled yet';

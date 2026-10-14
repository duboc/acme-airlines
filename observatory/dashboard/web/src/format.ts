export const usd = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

export const num = (n: number) => n.toLocaleString('en-US');

export const BILLING_EMPTY = 'Billing export not enabled yet';

export const GITHUB_EMPTY = "GitHub token isn't configured yet";

export function duration(seconds: number | null): string {
  if (seconds === null) return '—';
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Elapsed time from `iso` to `now` (both ISO strings), as "5m ago". */
export function ago(iso: string | null, now: string | null): string {
  if (!iso || !now) return '—';
  return `${duration(Math.max(0, (Date.parse(now) - Date.parse(iso)) / 1000))} ago`;
}

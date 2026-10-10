import { describe, expect, it } from 'vitest';
import { apiProxy } from '../devProxy';

describe('dev proxy', () => {
  it('strips the /api prefix because the backend serves routes at the root', () => {
    const { rewrite, target } = apiProxy['/api'];
    expect(target).toBe('http://localhost:8000');
    expect(rewrite('/api/journeys/J-1/events?limit=2')).toBe('/journeys/J-1/events?limit=2');
    expect(rewrite('/api/scenario/reset')).toBe('/scenario/reset');
  });
});

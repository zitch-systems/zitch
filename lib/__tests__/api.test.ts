// Exercise the apiJson degradation contract + the idempotency key. Mock the
// token (null → no 401/activity path), session, router and base URL so apiPost
// reduces to "call fetch and parse", which is what we want to assert.
jest.mock('@/lib/secureStore', () => ({ getToken: async () => null, clearSession: async () => {} }));
jest.mock('@/lib/session', () => ({ touchActivity: async () => {} }));
jest.mock('expo-router', () => ({ router: { replace: jest.fn() } }));
jest.mock('@/components/configFiles/apiConfig', () => ({ __esModule: true, default: 'https://test.local' }));

import { apiJson, newIdempotencyKey } from '@/lib/api';

const mockFetch = jest.fn();
// @ts-ignore - install a fetch stub
global.fetch = mockFetch;

beforeEach(() => mockFetch.mockReset());

describe('newIdempotencyKey', () => {
  it('produces a non-empty string with two segments', () => {
    const k = newIdempotencyKey();
    expect(typeof k).toBe('string');
    expect(k.split('-').length).toBeGreaterThanOrEqual(3);
  });
  it('is unique across calls', () => {
    const keys = new Set(Array.from({ length: 200 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(200);
  });
});

describe('apiJson', () => {
  it('parses a valid JSON body', async () => {
    mockFetch.mockResolvedValue({ status: 200, text: async () => JSON.stringify({ success: true, value: 42 }) });
    const res = await apiJson('/api/x/');
    expect(res).toEqual({ success: true, value: 42 });
  });

  it('degrades to the offline shape on a non-JSON body', async () => {
    mockFetch.mockResolvedValue({ status: 200, text: async () => '<html>502</html>' });
    const res = await apiJson('/api/x/');
    expect(res.success).toBe(false);
    expect(typeof res.message).toBe('string');
  });

  it('degrades to the offline shape on a network failure', async () => {
    mockFetch.mockRejectedValue(new Error('network down'));
    const res = await apiJson('/api/x/');
    expect(res.success).toBe(false);
    expect(res.message).toMatch(/unavailable/i);
  });
});

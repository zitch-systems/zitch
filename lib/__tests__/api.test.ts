// Exercise the apiJson degradation contract + the idempotency key. Mock the
// token (null → no 401/activity path), session, router and base URL so apiPost
// reduces to "call fetch and parse", which is what we want to assert.
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: jest.fn().mockReturnValue(0), getToken: async () => null, clearSession: async () => {} }));
jest.mock('@/lib/session', () => ({ touchActivity: async () => {} }));
jest.mock('expo-router', () => ({ router: { replace: jest.fn() } }));
jest.mock('@/components/configFiles/apiConfig', () => ({ __esModule: true, default: 'https://test.local' }));

import { apiJson, newIdempotencyKey } from '@/lib/api';
import { getSessionGeneration } from '@/lib/secureStore';

const mockFetch = jest.fn();
// @ts-ignore - install a fetch stub
global.fetch = mockFetch;

beforeEach(() => { mockFetch.mockReset(); (getSessionGeneration as jest.Mock).mockReturnValue(0); });

describe('newIdempotencyKey', () => {
  it('produces an unguessable non-empty token', () => {
    const k = newIdempotencyKey();
    expect(typeof k).toBe('string');
    expect(k.replace(/-/g, '')).toMatch(/^[a-f0-9]{32}$/i);
  });
  it('is unique across calls', () => {
    const keys = new Set(Array.from({ length: 200 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(200);
  });
});

describe('apiJson', () => {
  it('parses a valid JSON body', async () => {
    mockFetch.mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify({ success: true, value: 42 }) });
    const res = await apiJson('/api/x/');
    expect(res).toEqual({ success: true, value: 42, _httpOk: true, _httpStatus: 200 });
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


it.each([null, true, 'success', []])('treats non-object API JSON as an unknown response: %j', async body => {
  mockFetch.mockResolvedValue({ ok: true, status: 200, text: async () => JSON.stringify(body) });
  expect(await apiJson('/api/x/')).toMatchObject({ success: false, offline: true });
});


it('discards an old account response whose body arrives after an account change', async () => {
  let finishBody!: (value: string) => void;
  let reading!: () => void;
  const started = new Promise<void>(resolve => { reading = resolve; });
  const delayed = new Promise<string>(resolve => { finishBody = resolve; });
  mockFetch.mockResolvedValue({ ok: true, status: 200, text: () => { reading(); return delayed; } });
  const request = apiJson('/api/wallet_balance/');
  await started;
  (getSessionGeneration as jest.Mock).mockReturnValue(1);
  finishBody(JSON.stringify({ success: true, balance: 9000 }));
  expect(await request).toMatchObject({ success: false, offline: true });
});

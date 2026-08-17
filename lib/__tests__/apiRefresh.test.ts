/**
 * The refresh-on-401 path, and the one property that makes it safe to ship.
 *
 * The server burns a refresh token on first use and treats a SECOND presentation
 * as proof of theft, revoking the whole chain. The access token expires while the
 * app is closed, so the first screen after opening fires several authenticated
 * requests at once and they all 401 together. A per-request refresh would present
 * the same token N times — so the naive version of this feature signs the customer
 * out at exactly the moment it exists to keep them signed in, and files a
 * break-in report on the way. Hence the shared in-flight promise, and hence these
 * tests.
 */
jest.mock('expo-router', () => ({ router: { replace: jest.fn() } }));
jest.mock('expo-crypto', () => ({ randomUUID: () => 'uuid' }));
jest.mock('@/components/configFiles/apiConfig', () => ({ __esModule: true, default: 'https://api.test' }));
jest.mock('@/lib/session', () => ({ touchActivity: jest.fn() }));
jest.mock('@/lib/netPatch', () => ({ USER_AGENT: 'zitch-test', isEdgeBlockMessage: () => false }));
jest.mock('@/lib/deviceIntegrity', () => ({ deviceHeaders: async () => ({}) }));
jest.mock('@/lib/secureStore', () => ({
  getToken: jest.fn(),
  getRefreshToken: jest.fn(),
  saveToken: jest.fn(),
  saveRefreshToken: jest.fn(),
  clearSession: jest.fn(),
}));

const json = (status: number, body: any) =>
  ({ status, ok: status < 400, json: async () => body, text: async () => JSON.stringify(body) } as Response);

/** Paths of every fetch call, in order. */
const calledPaths = () =>
  (global.fetch as jest.Mock).mock.calls.map((c) => String(c[0]).replace('https://api.test', ''));

const authHeaders = () =>
  (global.fetch as jest.Mock).mock.calls.map((c) => c[1]?.headers?.Authorization);

// api.ts holds module state on purpose: `handlingExpiredSession` de-dupes the
// sign-out redirect for 1.5s so a burst of dead requests doesn't bounce the router
// repeatedly. That state would leak between these tests, so each one gets a fresh
// copy of the module.
let apiPost: typeof import('../api').apiPost;
// resetModules re-runs the jest.mock factory too, so the mock functions api.ts
// sees are new objects each time. They have to be re-read here rather than
// captured once at import, or the assertions watch a different pair of spies from
// the ones the code under test calls.
let mockGetToken: jest.Mock;
let mockGetRefresh: jest.Mock;
let mockSaveToken: jest.Mock;
let mockSaveRefresh: jest.Mock;
let mockClearSession: jest.Mock;

beforeEach(() => {
  jest.useRealTimers();
  jest.resetModules();
  const store = jest.requireMock('../secureStore') as Record<string, jest.Mock>;
  mockGetToken = store.getToken;
  mockGetRefresh = store.getRefreshToken;
  mockSaveToken = store.saveToken;
  mockSaveRefresh = store.saveRefreshToken;
  mockClearSession = store.clearSession;
  ({ apiPost } = jest.requireActual('../api') as typeof import('../api'));
  mockGetToken.mockResolvedValue('old-access');
  mockGetRefresh.mockResolvedValue('refresh-1');
  mockSaveToken.mockResolvedValue(undefined);
  mockSaveRefresh.mockResolvedValue(undefined);
  mockClearSession.mockResolvedValue(undefined);
  global.fetch = jest.fn();
});

describe('refresh on 401', () => {
  it('renews and retries the original request, so the caller never sees the 401', async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, { message: 'expired' }))
      .mockResolvedValueOnce(json(200, { access_token: 'new-access', refresh_token: 'refresh-2' }))
      .mockResolvedValueOnce(json(200, { success: true }));

    const res = await apiPost('/api/wallet/');

    expect(res.status).toBe(200);
    expect(calledPaths()).toEqual(['/api/wallet/', '/api/token/refresh/', '/api/wallet/']);
    expect(mockClearSession).not.toHaveBeenCalled();
  });

  it('stores the rotated pair before the retry goes out', async () => {
    // If the app died between using the new token and storing it, the next launch
    // would present the burnt one and be treated as a theft.
    const order: string[] = [];
    mockSaveRefresh.mockImplementation(async () => { order.push('save-refresh'); });
    mockSaveToken.mockImplementation(async () => { order.push('save-access'); });
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(200, { access_token: 'new-access', refresh_token: 'refresh-2' }))
      .mockImplementationOnce(async () => { order.push('retry'); return json(200, {}); });

    await apiPost('/api/wallet/');

    expect(order).toEqual(['save-refresh', 'save-access', 'retry']);
  });

  it('retries with the NEW access token, not the expired one it started with', async () => {
    mockGetToken
      .mockResolvedValueOnce('old-access')   // first attempt
      .mockResolvedValueOnce('old-access')   // the 401 check in apiPost
      .mockResolvedValue('new-access');     // after the refresh stored it
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(200, { access_token: 'new-access', refresh_token: 'refresh-2' }))
      .mockResolvedValueOnce(json(200, {}));

    await apiPost('/api/wallet/');

    const headers = authHeaders();
    expect(headers[0]).toBe('Bearer old-access');
    expect(headers[2]).toBe('Bearer new-access');
  });

  it('refreshes ONCE for a burst of simultaneous 401s', async () => {
    // The whole reason the in-flight promise is shared: presenting one refresh
    // token twice is indistinguishable from a theft, and the server revokes the
    // family for it.
    let firstRound = true;
    (global.fetch as jest.Mock).mockImplementation(async (url: string) => {
      const path = String(url);
      if (path.includes('/api/token/refresh/')) {
        firstRound = false;
        return json(200, { access_token: 'new-access', refresh_token: 'refresh-2' });
      }
      return firstRound ? json(401, {}) : json(200, {});
    });

    await Promise.all([apiPost('/api/wallet/'), apiPost('/api/profile/'), apiPost('/api/limits/')]);

    const refreshes = calledPaths().filter((p) => p === '/api/token/refresh/');
    expect(refreshes).toHaveLength(1);
    expect(mockClearSession).not.toHaveBeenCalled();
  });

  it('signs out when there is no refresh token at all', async () => {
    mockGetRefresh.mockResolvedValue(null);
    (global.fetch as jest.Mock).mockResolvedValue(json(401, {}));

    await apiPost('/api/wallet/');

    expect(calledPaths()).toEqual(['/api/wallet/']);
    expect(mockClearSession).toHaveBeenCalled();
  });

  it('signs out when the refresh itself is refused', async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(401, { message: 'Your session has expired. Please sign in again.' }));

    await apiPost('/api/wallet/');

    expect(mockClearSession).toHaveBeenCalled();
  });

  it('does not wipe a session when the refresh fails on the network', async () => {
    // A dropped connection is not an expired session. Signing the customer out
    // for it would turn a subway tunnel into a re-login.
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, {}))
      .mockRejectedValueOnce(new Error('Network request failed'));

    const res = await apiPost('/api/wallet/');

    expect(res.status).toBe(401);
    expect(mockClearSession).not.toHaveBeenCalled();
  });

  it('does not wipe a session when the refresh endpoint 5xxs', async () => {
    // A gateway error is not a verdict about the session.
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(502, { message: 'Bad gateway' }));

    await apiPost('/api/wallet/');

    expect(mockClearSession).not.toHaveBeenCalled();
  });

  it('gives up after one retry rather than looping', async () => {
    (global.fetch as jest.Mock)
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(200, { access_token: 'new-access', refresh_token: 'refresh-2' }))
      .mockResolvedValueOnce(json(401, {}));

    await apiPost('/api/wallet/');

    expect(calledPaths().filter((p) => p === '/api/token/refresh/')).toHaveLength(1);
    expect(mockClearSession).toHaveBeenCalled();
  });

  it('leaves an unauthenticated 401 alone — there is no session to renew', async () => {
    mockGetToken.mockResolvedValue(null);
    (global.fetch as jest.Mock).mockResolvedValue(json(401, { message: 'Incorrect details' }));

    await apiPost('/api/sigin/');

    expect(calledPaths()).toEqual(['/api/sigin/']);
    expect(mockClearSession).not.toHaveBeenCalled();
  });
});

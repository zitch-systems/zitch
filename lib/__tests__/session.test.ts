// In-memory AsyncStorage + a mockable getToken, so we can drive the idle/
// background lock state machine deterministically.
const mem: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: (k: string) => Promise.resolve(mem[k] ?? null),
    setItem: (k: string, v: string) => { mem[k] = v; return Promise.resolve(); },
    removeItem: (k: string) => { delete mem[k]; return Promise.resolve(); },
    multiRemove: (ks: string[]) => { ks.forEach((k) => delete mem[k]); return Promise.resolve(); },
  },
}));

const mockGetToken = jest.fn<Promise<string | null>, []>();
jest.mock('@/lib/secureStore', () => ({ getToken: () => mockGetToken() }));

import {
  IDLE_LIMIT_MS,
  LOCK_AFTER_BACKGROUND_MS,
  touchActivity,
  isSessionIdleExpired,
  enforceIdleTimeout,
  isSessionLocked,
  unlockSession,
  markBackgrounded,
  lockIfAwayTooLong,
  beginExternalActivity,
  endExternalActivity,
} from '@/lib/session';

beforeEach(() => {
  for (const k of Object.keys(mem)) delete mem[k];
  mockGetToken.mockReset();
});

describe('idle expiry', () => {
  it('is false when there is no session', async () => {
    mockGetToken.mockResolvedValue(null);
    expect(await isSessionIdleExpired()).toBe(false);
  });

  it('is false right after activity is stamped', async () => {
    mockGetToken.mockResolvedValue('tok');
    await touchActivity();
    expect(await isSessionIdleExpired()).toBe(false);
  });

  it('is true once activity is older than the idle limit', async () => {
    mockGetToken.mockResolvedValue('tok');
    mem['lastActiveAt'] = String(Date.now() - IDLE_LIMIT_MS - 1000);
    expect(await isSessionIdleExpired()).toBe(true);
  });

  it('does not force out when there is no activity stamp yet', async () => {
    mockGetToken.mockResolvedValue('tok'); // e.g. just signed in
    expect(await isSessionIdleExpired()).toBe(false);
  });
});

describe('enforceIdleTimeout', () => {
  it('locks the session when idle-expired and reports it', async () => {
    mockGetToken.mockResolvedValue('tok');
    mem['lastActiveAt'] = String(Date.now() - IDLE_LIMIT_MS - 1000);
    expect(await enforceIdleTimeout()).toBe(true);
    expect(await isSessionLocked()).toBe(true);
  });

  it('is a no-op when already locked', async () => {
    mockGetToken.mockResolvedValue('tok');
    mem['z-locked'] = '1';
    expect(await enforceIdleTimeout()).toBe(false);
  });

  it('unlockSession clears the lock and stamps activity', async () => {
    mem['z-locked'] = '1';
    await unlockSession();
    expect(await isSessionLocked()).toBe(false);
    expect(mem['lastActiveAt']).toBeDefined();
  });
});

describe('lockIfAwayTooLong', () => {
  it('does not lock for a brief background excursion', async () => {
    mockGetToken.mockResolvedValue('tok');
    await markBackgrounded();
    expect(await lockIfAwayTooLong()).toBe(false);
  });

  it('locks when away beyond the threshold with a live session', async () => {
    mockGetToken.mockResolvedValue('tok');
    mem['z-bg-at'] = String(Date.now() - LOCK_AFTER_BACKGROUND_MS - 1000);
    expect(await lockIfAwayTooLong()).toBe(true);
    expect(await isSessionLocked()).toBe(true);
  });

  it('never locks during an external activity (camera/picker)', async () => {
    mockGetToken.mockResolvedValue('tok');
    mem['z-bg-at'] = String(Date.now() - LOCK_AFTER_BACKGROUND_MS - 1000);
    beginExternalActivity();
    expect(await lockIfAwayTooLong()).toBe(false);
    endExternalActivity();
  });
});

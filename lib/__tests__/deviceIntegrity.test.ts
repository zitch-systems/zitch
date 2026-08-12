import * as SecureStore from 'expo-secure-store';
import * as Device from 'expo-device';

import { getDeviceId, getDeviceSignals, deviceHeaders, __resetDeviceCache } from '../deviceIntegrity';

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'whenUnlockedThisDeviceOnly',
}));

jest.mock('expo-crypto', () => ({
  getRandomBytes: jest.fn((count: number) =>
    Uint8Array.from({ length: count }, (_, index) => (index * 17 + 3) % 256)),
}));

jest.mock('expo-device', () => ({
  osVersion: '14',
  modelName: 'Pixel 7',
  brand: 'google',
  deviceName: "Ada's Pixel",
  isDevice: true,
  isRootedExperimentalAsync: jest.fn(),
}));

const store = SecureStore as jest.Mocked<typeof SecureStore>;
const device = Device as unknown as { isRootedExperimentalAsync: jest.Mock };

beforeEach(() => {
  jest.clearAllMocks();
  __resetDeviceCache();
  device.isRootedExperimentalAsync.mockResolvedValue(false);
});

describe('getDeviceId', () => {
  it('reuses the stored id so a device stays recognisable across launches', async () => {
    store.getItemAsync.mockResolvedValue('existing-id');
    expect(await getDeviceId()).toBe('existing-id');
    expect(store.setItemAsync).not.toHaveBeenCalled();
  });

  it('creates and stores an id on first run', async () => {
    store.getItemAsync.mockResolvedValue(null);
    const id = await getDeviceId();
    expect(id).toHaveLength(40);
    expect(store.setItemAsync).toHaveBeenCalledWith('z-device-id', id, {
      // Not synced to iCloud and not restored onto another handset: an id that
      // survived a backup restore would vouch for a device the user never used.
      keychainAccessible: 'whenUnlockedThisDeviceOnly',
    });
  });

  it('caches within the process instead of hitting the keychain per request', async () => {
    store.getItemAsync.mockResolvedValue('existing-id');
    await getDeviceId();
    await getDeviceId();
    expect(store.getItemAsync).toHaveBeenCalledTimes(1);
  });

  it('degrades to an ephemeral id when the keychain fails', async () => {
    // Telemetry must never take the app down; a worse risk score beats no app.
    store.getItemAsync.mockRejectedValue(new Error('keychain locked'));
    expect(await getDeviceId()).toMatch(/^eph-/);
  });
});

describe('getDeviceSignals', () => {
  beforeEach(() => store.getItemAsync.mockResolvedValue('dev-1'));

  it('reports coarse hardware facts', async () => {
    const s = await getDeviceSignals();
    expect(s).toMatchObject({
      id: 'dev-1', osVersion: '14', model: 'Pixel 7', brand: 'google',
      physical: true, rooted: false,
    });
  });

  it('reports rooted as null — not false — when the check cannot run', async () => {
    // "We could not check" and "the device is clean" must score differently, and a
    // check that throws on a tampered OS is itself a signal.
    device.isRootedExperimentalAsync.mockRejectedValue(new Error('nope'));
    expect((await getDeviceSignals()).rooted).toBeNull();
  });
});

describe('deviceHeaders', () => {
  beforeEach(() => store.getItemAsync.mockResolvedValue('dev-1'));

  it('sends the id separately from the descriptor', async () => {
    const h = await deviceHeaders();
    expect(h['X-Zitch-Device']).toBe('dev-1');
    expect(h['X-Zitch-Device-Info']).toContain('model=Pixel 7');
    expect(h['X-Zitch-Device-Info']).toContain('rooted=0');
  });

  it('never sends the device name', async () => {
    // Device.deviceName is routinely a person's real name.
    const h = await deviceHeaders();
    expect(JSON.stringify(h)).not.toContain('Ada');
  });

  it('omits rooted entirely rather than guessing when unknown', async () => {
    device.isRootedExperimentalAsync.mockRejectedValue(new Error('nope'));
    expect((await deviceHeaders())['X-Zitch-Device-Info']).not.toContain('rooted');
  });

  it('flags a rooted device without blocking anything client-side', async () => {
    device.isRootedExperimentalAsync.mockResolvedValue(true);
    const h = await deviceHeaders();
    expect(h['X-Zitch-Device-Info']).toContain('rooted=1');
    // Still a normal header set — the client does not refuse to talk to the API. A
    // client-side block only teaches an attacker to flip one boolean, while locking
    // out honest users who root their own phones.
    expect(h['X-Zitch-Device']).toBe('dev-1');
  });

  it('bounds the descriptor so a hostile value cannot bloat every request', async () => {
    (Device as unknown as { modelName: string }).modelName = 'x'.repeat(500);
    expect((await deviceHeaders())['X-Zitch-Device-Info'].length).toBeLessThanOrEqual(200);
    (Device as unknown as { modelName: string }).modelName = 'Pixel 7';
  });
});

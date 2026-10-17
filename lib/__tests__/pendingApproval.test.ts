import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  pendingWhatsAppApproval,
  rememberWhatsAppApproval,
  rememberWhatsAppApprovalUrl,
  clearPendingWhatsAppApproval,
} from '../pendingApproval';

let mockSessionGeneration = 0;
jest.mock('@/lib/secureStore', () => ({ getSessionGeneration: () => mockSessionGeneration }));

jest.mock('expo-secure-store', () => ({
  WHEN_UNLOCKED_THIS_DEVICE_ONLY: 1,
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  removeItem: jest.fn().mockResolvedValue(undefined),
}));

const storage = SecureStore as jest.Mocked<typeof SecureStore>;
const legacy = AsyncStorage as jest.Mocked<typeof AsyncStorage>;
let nativeValue: string | null = null;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockSessionGeneration = 0;
  nativeValue = null;
  storage.getItemAsync.mockReset().mockImplementation(async () => nativeValue);
  storage.setItemAsync.mockReset().mockImplementation(async (_key, value) => { nativeValue = value; });
  storage.deleteItemAsync.mockReset().mockImplementation(async () => { nativeValue = null; });
  legacy.removeItem.mockReset().mockResolvedValue(undefined);
});

describe('pending WhatsApp approval hand-off', () => {
  it.each([
    ['zitch://waapprove?token=ap7.signed_token', 'ap7.signed_token'],
    ['https://api.zitch.ng/wa/approve/ap8.signed-token', 'ap8.signed-token'],
  ])('captures a supported link: %s', async (url, token) => {
    await rememberWhatsAppApprovalUrl(url);
    expect(storage.setItemAsync).toHaveBeenCalledWith(
      'z-pending-wa-approval',
      expect.stringContaining(`"token":"${token}"`),
      expect.objectContaining({ keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY }),
    );
    expect(legacy.removeItem).toHaveBeenCalledWith('z-pending-wa-approval');
  });

  it('ignores token parameters on unrelated links', async () => {
    await rememberWhatsAppApprovalUrl('https://attacker.test/wa/approve/ap9.forged');
    await rememberWhatsAppApprovalUrl('zitch://home?token=ap9.forged');
    expect(storage.setItemAsync).not.toHaveBeenCalled();
  });

  it('rejects future-dated hand-offs instead of extending their validity', async () => {
    storage.getItemAsync.mockResolvedValue(JSON.stringify({ token: 'ap.future', savedAt: Date.now() + 60000 }));
    await expect(pendingWhatsAppApproval()).resolves.toBe('');
    expect(storage.deleteItemAsync).toHaveBeenCalledWith('z-pending-wa-approval');
  });

  it('burns a stored hand-off after ten minutes', async () => {
    storage.getItemAsync.mockResolvedValue(JSON.stringify({
      token: 'ap10.signed',
      savedAt: Date.now() - (10 * 60 * 1000) - 1,
    }));
    await expect(pendingWhatsAppApproval()).resolves.toBe('');
    expect(storage.deleteItemAsync).toHaveBeenCalledWith('z-pending-wa-approval');
  });

  it('clears a completed approval only for the matching token and session', async () => {
    await rememberWhatsAppApproval('ap1.signed');
    await clearPendingWhatsAppApproval('ap1.signed', 0);
    await expect(pendingWhatsAppApproval()).resolves.toBe('');
    expect(storage.deleteItemAsync).toHaveBeenCalledTimes(1);
  });

  it('preserves a newer stored token when an older request finishes', async () => {
    await rememberWhatsAppApproval('ap2.signed');
    await clearPendingWhatsAppApproval('ap1.signed', 0);
    await expect(pendingWhatsAppApproval()).resolves.toBe('ap2.signed');
    expect(storage.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('does not clear an approval belonging to a changed session', async () => {
    await rememberWhatsAppApproval('ap1.signed');
    mockSessionGeneration = 1;
    await clearPendingWhatsAppApproval('ap1.signed', 0);
    await expect(pendingWhatsAppApproval()).resolves.toBe('ap1.signed');
    expect(storage.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('rechecks session ownership after a delayed native read', async () => {
    await rememberWhatsAppApproval('ap1.signed');
    const read = deferred<string | null>();
    const started = deferred<void>();
    storage.getItemAsync.mockImplementationOnce(() => { started.resolve(); return read.promise; });
    const clearing = clearPendingWhatsAppApproval('ap1.signed', 0);
    await started.promise;
    mockSessionGeneration = 1;
    read.resolve(nativeValue);
    await clearing;
    await expect(pendingWhatsAppApproval()).resolves.toBe('ap1.signed');
    expect(storage.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('preserves a new link arriving while a native delete is in flight', async () => {
    await rememberWhatsAppApproval('ap1.signed');
    const deletion = deferred<void>();
    const started = deferred<void>();
    storage.deleteItemAsync.mockImplementationOnce(async () => {
      started.resolve();
      await deletion.promise;
      nativeValue = null;
    });
    const clearing = clearPendingWhatsAppApproval('ap1.signed', 0);
    await started.promise;
    const remembering = rememberWhatsAppApproval('ap2.signed');
    deletion.resolve();
    await Promise.all([clearing, remembering]);
    await expect(pendingWhatsAppApproval()).resolves.toBe('ap2.signed');
  });

  it('keeps a newer link when native writes finish slowly', async () => {
    const writing = deferred<void>();
    const started = deferred<void>();
    storage.setItemAsync.mockImplementationOnce(async (_key, value) => {
      started.resolve();
      await writing.promise;
      nativeValue = value;
    });
    const first = rememberWhatsAppApproval('ap1.signed');
    await started.promise;
    const second = rememberWhatsAppApproval('ap2.signed');
    writing.resolve();
    await Promise.all([first, second]);
    await expect(pendingWhatsAppApproval()).resolves.toBe('ap2.signed');
  });

  it('preserves explicit unconditional clear for existing callers', async () => {
    await rememberWhatsAppApproval('ap1.signed');
    await clearPendingWhatsAppApproval();
    await expect(pendingWhatsAppApproval()).resolves.toBe('');
  });
});

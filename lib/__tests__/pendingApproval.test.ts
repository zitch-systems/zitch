import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  pendingWhatsAppApproval,
  rememberWhatsAppApprovalUrl,
} from '../pendingApproval';

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

beforeEach(() => {
  jest.clearAllMocks();
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

  it('burns a stored hand-off after ten minutes', async () => {
    storage.getItemAsync.mockResolvedValue(JSON.stringify({
      token: 'ap10.signed',
      savedAt: Date.now() - (10 * 60 * 1000) - 1,
    }));
    await expect(pendingWhatsAppApproval()).resolves.toBe('');
    expect(storage.deleteItemAsync).toHaveBeenCalledWith('z-pending-wa-approval');
  });
});

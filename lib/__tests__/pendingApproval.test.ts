import AsyncStorage from '@react-native-async-storage/async-storage';

import {
  pendingWhatsAppApproval,
  rememberWhatsAppApprovalUrl,
} from '../pendingApproval';

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn(),
}));

const storage = AsyncStorage as jest.Mocked<typeof AsyncStorage>;

beforeEach(() => {
  jest.clearAllMocks();
});

describe('pending WhatsApp approval hand-off', () => {
  it.each([
    ['zitch://waapprove?token=ap7.signed_token', 'ap7.signed_token'],
    ['https://api.zitch.ng/wa/approve/ap8.signed-token', 'ap8.signed-token'],
  ])('captures a supported link: %s', async (url, token) => {
    await rememberWhatsAppApprovalUrl(url);
    expect(storage.setItem).toHaveBeenCalledWith(
      'z-pending-wa-approval',
      expect.stringContaining(`"token":"${token}"`),
    );
  });

  it('ignores token parameters on unrelated links', async () => {
    await rememberWhatsAppApprovalUrl('https://attacker.test/wa/approve/ap9.forged');
    await rememberWhatsAppApprovalUrl('zitch://home?token=ap9.forged');
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('burns a stored hand-off after ten minutes', async () => {
    storage.getItem.mockResolvedValue(JSON.stringify({
      token: 'ap10.signed',
      savedAt: Date.now() - (10 * 60 * 1000) - 1,
    }));
    await expect(pendingWhatsAppApproval()).resolves.toBe('');
    expect(storage.removeItem).toHaveBeenCalledWith('z-pending-wa-approval');
  });
});

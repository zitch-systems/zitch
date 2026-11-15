const mockGet = jest.fn();
const mockRequest = jest.fn();
const mockPick = jest.fn();
jest.mock('expo-contacts', () => ({
  getPermissionsAsync: () => mockGet(),
  requestPermissionsAsync: () => mockRequest(),
  presentContactPickerAsync: () => mockPick(),
}));
jest.mock('@/lib/session', () => ({ beginExternalActivity: jest.fn(), endExternalActivity: jest.fn() }));

import { Alert } from 'react-native';
import { nigerianNumber, pickContactPhone } from '@/lib/contacts';

describe('nigerianNumber', () => {
  it.each([
    ['+234 803 123 4567', '08031234567'],
    ['2348031234567', '08031234567'],
    ['0803-123-4567', '08031234567'],
    ['8031234567', '08031234567'],
    ['+44 20 7946 0958', ''],
    ['12345', ''],
  ])('%s → %s', (raw, out) => expect(nigerianNumber(raw)).toBe(out));
});

describe('pickContactPhone', () => {
  beforeEach(() => { jest.clearAllMocks(); });

  it('asks for permission before opening the picker', async () => {
    mockGet.mockResolvedValue({ granted: false, canAskAgain: true });
    mockRequest.mockResolvedValue({ granted: true });
    mockPick.mockResolvedValue({ name: 'Ada', phoneNumbers: [{ number: '+2348031234567' }] });
    await expect(pickContactPhone()).resolves.toEqual({ ok: true, contact: { name: 'Ada', phone: '08031234567' } });
    expect(mockRequest).toHaveBeenCalled();
  });

  it('offers Settings and never opens the picker when refused', async () => {
    const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => {});
    mockGet.mockResolvedValue({ granted: false, canAskAgain: false });
    await expect(pickContactPhone()).resolves.toEqual({ ok: false, reason: 'denied' });
    expect(mockPick).not.toHaveBeenCalled();
    expect(alert).toHaveBeenCalled();
  });

  it('reports a contact with no Nigerian mobile number', async () => {
    mockGet.mockResolvedValue({ granted: true });
    mockPick.mockResolvedValue({ name: 'Bob', phoneNumbers: [{ number: '+44 20 7946 0958' }] });
    await expect(pickContactPhone()).resolves.toEqual({ ok: false, reason: 'no-number' });
  });

  it('a cancelled picker is not an error', async () => {
    mockGet.mockResolvedValue({ granted: true });
    mockPick.mockResolvedValue(null);
    await expect(pickContactPhone()).resolves.toEqual({ ok: false, reason: 'cancelled' });
  });
});

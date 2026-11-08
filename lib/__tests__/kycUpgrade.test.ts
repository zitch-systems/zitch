import { banking } from '@/lib/services/banking';
const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));
beforeEach(() => { mockApiJson.mockReset(); mockApiJson.mockResolvedValue({ success: true }); });

it.each(['bvn', 'nin'] as const)('starts %s with the verified profile and bank method', async (kind) => {
  const profile = { otherNames: 'Ada', lastName: 'Eze', phoneNo: '08011112222', dateOfBirth: '01/01/1990', address: '1 Test Road' };
  await banking.identity(kind, '12345678901', profile);
  expect(mockApiJson).toHaveBeenCalledWith('/api/banking/identity/start/', { kind, identifier: '12345678901', profile, method: 'OTP' });
});
it('binds confirmation to the bank reference without resending identity data', async () => {
  await banking.confirm('bank-ref', '123456');
  expect(mockApiJson).toHaveBeenCalledWith('/api/banking/identity/confirm/', { reference: 'bank-ref', otp: '123456' });
});
it('submits the full bank upgrade profile and server-owned address reference', async () => {
  const profile = { bvn: '12345678901', nin: '23456789012', utilityBill: 'utility-image', userPhoto: 'face-image', idType: '1' };
  await banking.upgrade(3, profile, 'address-ref');
  expect(mockApiJson).toHaveBeenCalledWith('/api/banking/upgrade/', { tier: 3, profile, address_reference: 'address-ref' });
});
it('keeps Prembly address submission separate from bank identity verification', async () => {
  await banking.address({ house_number: '1', street: 'Test Road', city: 'Lagos', address: '1 Test Road, Lagos' });
  expect(mockApiJson).toHaveBeenCalledWith('/api/banking/address/start/', { address: expect.objectContaining({ house_number: '1' }) });
});

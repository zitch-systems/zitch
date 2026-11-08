import { EP } from '@/lib/endpoints';
import { kycService, resolveOwnershipOtpRoute } from '@/lib/services/kyc';

const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: unknown[]) => mockApiJson(...args) }));

beforeEach(() => { mockApiJson.mockReset(); mockApiJson.mockResolvedValue({ success: true }); });

it('accepts an explicit Prembly SMS challenge without bank tracking and rejects an ambiguous one', () => {
  expect(resolveOwnershipOtpRoute({ success: true, otp_required: true, identity_verification_provider: 'prembly' })).toEqual({ trackingId: '' });
  expect(resolveOwnershipOtpRoute({ success: true, otp_required: true })).toBeNull();
  expect(resolveOwnershipOtpRoute({ success: false, otp_required: true, identity_verification_provider: 'prembly' })).toBeNull();
  expect(resolveOwnershipOtpRoute({ success: true, otp_required: true, tracking_id: 'BANK-1' })).toEqual({ trackingId: 'BANK-1' });
});

it('starts NIN on the identity endpoint so the server selects the rail', async () => {
  await kycService.startNin('12345678901');
  expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.nin, { nin: '12345678901' });
});

it.each(['bvn', 'nin'] as const)('confirms %s ownership without a legacy tracking field or raw identifier', async (kind) => {
  if (kind === 'bvn') await kycService.confirmBvn('', '123456');
  else await kycService.confirmNin('', '123456');
  expect(mockApiJson).toHaveBeenCalledWith(kind === 'bvn' ? EP.kyc.bvnConfirm : EP.kyc.ninConfirm, { otp: '123456' });
});

it.each(['bvn', 'nin'] as const)('resends %s through the original identity lookup for an untracked provider challenge', async (kind) => {
  if (kind === 'bvn') await kycService.resendBvn('', '12345678901');
  else await kycService.resendNin('', '12345678901');
  expect(mockApiJson).toHaveBeenCalledWith(kind === 'bvn' ? EP.kyc.bvnStart : EP.kyc.nin, { [kind]: '12345678901' });
});

it('preserves the bank tracking reference for an existing legacy OTP', async () => {
  await kycService.confirmNin('BANK-1', '123456');
  expect(mockApiJson).toHaveBeenLastCalledWith(EP.kyc.ninConfirm, { tracking_id: 'BANK-1', otp: '123456' });
  await kycService.resendNin('BANK-1', '');
  expect(mockApiJson).toHaveBeenLastCalledWith(EP.wallet.wemaResendOtp, { tracking_id: 'BANK-1' });
});

// Services are thin typed wrappers over apiJson — assert each calls the right
// endpoint with the right body shape (so a path/param regression is caught).
const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: any[]) => mockApiJson(...args) }));

import { classifyKycResponse, isAccountOtpPending, kycService, resolveIdentityOtpRoute } from '@/lib/services/kyc';
import { walletService } from '@/lib/services/wallet';
import { transfersService } from '@/lib/services/transfers';
import { loansService } from '@/lib/services/loans';
import { cardsService } from '@/lib/services/cards';
import { EP } from '@/lib/endpoints';

beforeEach(() => {
  mockApiJson.mockReset();
  mockApiJson.mockResolvedValue({ success: true });
});

describe('kycService', () => {
  it('getStatus hits the status endpoint', async () => {
    await kycService.getStatus();
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.status);
  });
  it('startBvn posts the bvn', async () => {
    mockApiJson.mockResolvedValueOnce({ success: true, otp_required: true, tracking_id: 'bvn-track-1' });
    await kycService.startBvn('22222222222');
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnStart, { bvn: '22222222222' });
  });
  it('confirms BVN with the server tracking reference', async () => {
    await kycService.confirmBvn('bvn-track-1', '123456');
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnConfirm, {
      tracking_id: 'bvn-track-1', otp: '123456',
    });
  });
  it('resends Wema OTP with the same tracking reference', async () => {
    await kycService.resendBvn('bvn-track-1');
    expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.wemaResendOtp, { tracking_id: 'bvn-track-1' });
  });
  it('posts structured residential address fields', async () => {
    await kycService.verifyAddress({
      buildingNumber: '12', apartment: '2B', street: 'Allen Avenue', city: 'Ikeja',
      town: 'Ikeja', state: 'Lagos', lga: 'Ikeja', lcda: 'Ikeja', landmark: 'Market',
      additionalInformation: 'Behind the pharmacy', country: 'Nigeria', fullAddress: '', postalCode: '100001',
    }, 'proof-b64');
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.address, {
      residentialAddress: expect.objectContaining({
        buildingNumber: '12', apartment: '2B', street: 'Allen Avenue', city: 'Ikeja',
        state: 'Lagos', lga: 'Ikeja', country: 'Nigeria', fullAddress: '12 2B Allen Avenue, Ikeja, Lagos',
      }),
      document: 'proof-b64',
    });
  });
  it('keeps pending and identity review distinct from verified success', () => {
    expect(classifyKycResponse({ success: false, pending: true })).toBe('pending');
    expect(classifyKycResponse({ success: true, identity_review_required: true })).toBe('review');
    expect(classifyKycResponse({ success: true }, ['bvn_verified'])).toBe('unverified');
    expect(classifyKycResponse({ success: true, bvn_verified: false }, ['bvn_verified'])).toBe('unverified');
    expect(classifyKycResponse({ success: true, bvn_verified: true }, ['bvn_verified'])).toBe('success');
    expect(classifyKycResponse({ success: true })).toBe('success');
    expect(classifyKycResponse({ success: false, identity_review_required: false })).toBe('error');
  });
  it('routes face-start account OTP to the server-selected identity tracking flow', () => {
    const response = {
      success: true,
      status: 'account_otp_pending',
      account_setup_state: 'otp_pending' as const,
      tracking_id: 'nin-track-1',
      using_bvn: false,
      otp_destination_kind: 'nin',
    };
    expect(isAccountOtpPending(response)).toBe(true);
    expect(resolveIdentityOtpRoute(response, 'bvn')).toEqual({ kind: 'nin', trackingId: 'nin-track-1' });
    expect(resolveIdentityOtpRoute({ ...response, tracking_id: '' }, 'bvn')).toBeNull();
    expect(resolveIdentityOtpRoute({ ...response, status: 'verified' }, 'bvn')).toBeNull();
  });
  it('verifyNin posts nin + image', async () => {
    await kycService.verifyNin('11111111111', 'b64');
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.nin, { nin: '11111111111', nin_image: 'b64' });
  });
});

describe('walletService', () => {
  it('getBalance hits the balance endpoint', async () => {
    await walletService.getBalance();
    expect(mockApiJson).toHaveBeenCalledWith(EP.wallet.balance);
  });
});

describe('transfersService', () => {
  it('resolve scopes to a bank when a code is given (key is `bank`)', async () => {
    await transfersService.resolve('0123456789', '058');
    expect(mockApiJson).toHaveBeenCalledWith(EP.transfers.resolve, { account_number: '0123456789', bank: '058' });
  });
  it('resolve omits the bank for auto-detect', async () => {
    await transfersService.resolve('0123456789');
    expect(mockApiJson).toHaveBeenCalledWith(EP.transfers.resolve, { account_number: '0123456789' });
  });
  it('send passes the body through (incl. idempotency key)', async () => {
    const body = { account_number: '0123456789', bank: '058', amount: 5000, transaction_pin: '1234', idempotency_key: 'idem-key-1' };
    await transfersService.send(body);
    expect(mockApiJson).toHaveBeenCalledWith(EP.transfers.send, body);
  });
});

describe('loansService', () => {
  it('attaches the stable idempotency key to a loan request', async () => {
    await loansService.request(50000, 30, '1234', 'loan-request-key-1');
    expect(mockApiJson).toHaveBeenCalledWith(EP.loans.request, {
      amount: '50000',
      tenure_days: 30,
      transaction_pin: '1234',
      idempotency_key: 'loan-request-key-1',
    });
  });
});

describe('cardsService', () => {
  it('attaches the stable idempotency key to a card issuance request', async () => {
    await cardsService.create('card-issue-key-1');
    expect(mockApiJson).toHaveBeenCalledWith(EP.cards.create, {
      idempotency_key: 'card-issue-key-1',
    });
  });
});

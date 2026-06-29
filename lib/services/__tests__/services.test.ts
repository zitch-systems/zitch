// Services are thin typed wrappers over apiJson — assert each calls the right
// endpoint with the right body shape (so a path/param regression is caught).
const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: any[]) => mockApiJson(...args) }));

import { kycService } from '@/lib/services/kyc';
import { walletService } from '@/lib/services/wallet';
import { transfersService } from '@/lib/services/transfers';
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
    await kycService.startBvn('22222222222');
    expect(mockApiJson).toHaveBeenCalledWith(EP.kyc.bvnStart, { bvn: '22222222222' });
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
  it('resolve posts account number + bank code', async () => {
    await transfersService.resolve('0123456789', '058');
    expect(mockApiJson).toHaveBeenCalledWith(EP.transfers.resolve, { account_number: '0123456789', bank_code: '058' });
  });
  it('send always includes the idempotency key', async () => {
    await transfersService.send({ amount: 5000 }, 'idem-key-1');
    expect(mockApiJson).toHaveBeenCalledWith(EP.transfers.send, { amount: 5000, idempotency_key: 'idem-key-1' });
  });
});

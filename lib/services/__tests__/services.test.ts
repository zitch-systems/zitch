// Services are thin typed wrappers over apiJson — assert each calls the right
// endpoint with the right body shape (so a path/param regression is caught).
const mockApiJson = jest.fn();
jest.mock('@/lib/api', () => ({ apiJson: (...args: any[]) => mockApiJson(...args) }));

import { kycService } from '@/lib/services/kyc';
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
  it('starts and confirms the authenticated email challenge', async () => {
    await kycService.startEmail('ada@example.com');
    expect(mockApiJson).toHaveBeenLastCalledWith(EP.auth.emailVerifyStart, { email: 'ada@example.com' });
    await kycService.confirmEmail('123456');
    expect(mockApiJson).toHaveBeenLastCalledWith(EP.auth.emailVerifyConfirm, { otp: '123456' });
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

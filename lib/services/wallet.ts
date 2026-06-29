// Wallet service — typed wrappers over the wallet endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

export type WalletBalance = ApiResult<{
  wallet: number | string;
  user_first_name?: string;
  user_last_name?: string;
  user_avatar?: string;
  account_number?: string;
  bank_name?: string;
}>;

export type TransactionHistory = ApiResult<{
  all_site_transactions?: any[];
}>;

export type VirtualAccount = ApiResult<{
  account_number?: string;
  bank_name?: string;
  account_name?: string;
}>;

export const walletService = {
  getBalance: () => apiJson<WalletBalance>(EP.wallet.balance),
  getHistory: () => apiJson<TransactionHistory>(EP.wallet.history),
  // Dedicated (virtual) account: fetch the existing one, or provision via BVN.
  getAccount: () => apiJson<VirtualAccount>(EP.wallet.account),
  createAccount: (bvn: string) => apiJson<VirtualAccount>(EP.wallet.createAccount, { bvn }),
};

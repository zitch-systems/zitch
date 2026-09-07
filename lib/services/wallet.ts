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
  otp_required?: boolean;
  tracking_id?: string;
  using_bvn?: boolean;
  otp_destination?: string;
  otp_destination_kind?: string;
  bvn_verified?: boolean;
  nin_verified?: boolean;
  tier?: number;
}>;

export const walletService = {
  getBalance: () => apiJson<WalletBalance>(EP.wallet.balance),
  getHistory: () => apiJson<TransactionHistory>(EP.wallet.history),
  // Dedicated (virtual) account: fetch the existing one, or start BVN/NIN OTP provisioning.
  getAccount: () => apiJson<VirtualAccount>(EP.wallet.account),
  createAccount: (identity: { bvn?: string; nin?: string } | string) =>
    apiJson<VirtualAccount>(
      EP.wallet.createAccount,
      typeof identity === 'string' ? { bvn: identity } : identity,
    ),
  verifyWemaOtp: (trackingId: string, otp: string, identity: { bvn?: string; nin?: string } = {}) =>
    apiJson<VirtualAccount>(EP.wallet.wemaVerifyOtp, { tracking_id: trackingId, otp, ...identity }),
  resendWemaOtp: (trackingId: string) =>
    apiJson<VirtualAccount>(EP.wallet.wemaResendOtp, { tracking_id: trackingId }),
};

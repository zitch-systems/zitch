// Wallet service — typed wrappers over the wallet endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type CapabilityPayload = {
  provider?: string;
  legacy_funding_review?: boolean;
  account_setup_state?: string;
  spending_available?: boolean;
  bill_payments_available?: boolean;
  transfers_available?: boolean;
};

export type WalletCapabilities = {
  billPaymentsAvailable: boolean;
  transfersAvailable: boolean;
};

type BalancePayload = CapabilityPayload & {
  wallet?: number | string;
  available_balance?: number | string;
  historical_balance?: number | string;
  legacy_funding_balance?: number | string;
};

export type WalletBalances = {
  totalBalance: number;
  availableBalance: number;
  historicalBalance: number;
};

const finiteAmount = (value: number | string | undefined): number => {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
};

export const walletBalances = (value: BalancePayload | null | undefined): WalletBalances => {
  const totalBalance = finiteAmount(value?.wallet);
  const capabilities = walletCapabilities(value);
  const availableBalance = capabilities.billPaymentsAvailable || capabilities.transfersAvailable
    ? Math.max(0, Math.min(totalBalance, value?.available_balance == null
      ? totalBalance : finiteAmount(value.available_balance))) : 0;
  return {
    totalBalance,
    availableBalance,
    historicalBalance: value?.historical_balance == null
      ? Math.max(0, totalBalance - availableBalance)
      : Math.max(0, finiteAmount(value.historical_balance)),
  };
};

export const walletCapabilities = (value: CapabilityPayload | null | undefined): WalletCapabilities => {
  if (value?.provider !== 'partnership' || value.legacy_funding_review === true
      || value.account_setup_state === 'legacy_funding_review' || value.spending_available === false) {
    return { billPaymentsAvailable: false, transfersAvailable: false };
  }
  return {
    billPaymentsAvailable: value.bill_payments_available == null ? true : value.bill_payments_available === true,
    transfersAvailable: value.transfers_available == null ? true : value.transfers_available === true,
  };
};

export const walletCapabilityMessage = ({ billPaymentsAvailable, transfersAvailable }: WalletCapabilities): string => {
  if (billPaymentsAvailable && transfersAvailable) return '';
  if (billPaymentsAvailable) return 'Bill payments are available. Transfers are currently unavailable.';
  if (transfersAvailable) return 'Bill payments are currently unavailable.';
  return 'Transfers and bill payments are currently unavailable.';
};

export type WalletBalance = ApiResult<BalancePayload & {
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

export type FundingAccount = ApiResult<CapabilityPayload & {
  has_account?: boolean;
  available?: boolean;
  can_fund?: boolean;
  migration_message?: string;
  partnership_setup_required?: boolean;
  account_number?: string;
  bank_name?: string;
  account_name?: string;
  otp_required?: boolean;
  tracking_id?: string;
  using_bvn?: boolean;
  otp_destination?: string;
  delivery_notice?: string;
  delivery?: string;
  identity_verification_provider?: 'prembly' | 'wema';
  otp_destination_kind?: string;
  pending?: boolean;
  identity_review_required?: boolean;
  bvn_verified?: boolean;
  nin_verified?: boolean;
  tier?: number;
  tier_name?: string;
  transaction_limit?: string;
  daily_transfer_limit?: string;
  daily_bill_limit?: string;
  face_verified?: boolean;
  address_verified?: boolean;
  id_document_verified?: boolean;
  identity_upgrade_required?: boolean;
  upgrade_required?: boolean;
  upgraded?: boolean;
}>;

/** Never present an unconfirmed or historical account as a funding destination. */
export const isFundingAccountReady = (value: FundingAccount | null | undefined): boolean =>
  value?.provider === 'partnership' && value.can_fund === true
  && value.available === true && value.has_account === true
  && value.legacy_funding_review !== true
  && ['ready', 'bank_history_review'].includes(value.account_setup_state || '')
  && /^\d{10}$/.test(value.account_number || '') && !/^711/.test(value.account_number || '');

export const walletService = {
  getBalance: () => apiJson<WalletBalance>(EP.wallet.balance),
  getHistory: () => apiJson<TransactionHistory>(EP.wallet.history),
  // Partnership funding account: fetch current eligibility or start bank identity verification.
  getAccount: () => apiJson<FundingAccount>(EP.wallet.account),
  createAccount: (identity: { bvn?: string; nin?: string } | string) =>
    apiJson<FundingAccount>(
      EP.wallet.createAccount,
      typeof identity === 'string' ? { bvn: identity } : identity,
    ),
  verifyWemaOtp: (trackingId: string, otp: string, identity: { bvn?: string; nin?: string } = {}) =>
    apiJson<FundingAccount>(EP.wallet.wemaVerifyOtp, { tracking_id: trackingId, otp, ...identity }),
  resendWemaOtp: (trackingId: string) =>
    apiJson<FundingAccount>(EP.wallet.wemaResendOtp, { tracking_id: trackingId }),
  // One request, or nothing: the bank scores BVN, NIN and the live image
  // together for an account it has already opened. `live_image` is base64 with
  // no data: prefix, matching kycService.verifyFace.
  upgradeTier2: (bvn: string, nin: string, liveImage: string) =>
    apiJson<FundingAccount>(EP.wallet.wemaUpgradeTier2,
                            { bvn, nin, live_image: liveImage }),
};

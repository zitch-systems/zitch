// Wallet service — typed wrappers over the wallet endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type CapabilityPayload = {
  provider?: 'partnership' | 'wema_vas';
  test_mode?: boolean;
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
  vas_balance?: number | string;
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
  if (value?.provider !== 'wema_vas' && value?.available_balance == null) {
    return { totalBalance, availableBalance: totalBalance, historicalBalance: 0 };
  }
  const capabilities = walletCapabilities(value);
  const availableBalance = capabilities.billPaymentsAvailable || capabilities.transfersAvailable
    ? Math.max(0, Math.min(totalBalance, finiteAmount(value.available_balance))) : 0;
  return {
    totalBalance,
    availableBalance,
    historicalBalance: value.historical_balance == null
      ? Math.max(0, totalBalance - finiteAmount(value.vas_balance))
      : Math.max(0, finiteAmount(value.historical_balance)),
  };
};

export const walletCapabilities = (value: CapabilityPayload | null | undefined): WalletCapabilities => {
  if (value?.provider === 'wema_vas' && value.test_mode === true) {
    return { billPaymentsAvailable: false, transfersAvailable: false };
  }
  // Older Partnership responses expose one spending flag. VAS requires an
  // explicit capability so a partial response cannot promise usable bill funds.
  const legacyAvailable = (value?.provider === 'partnership' && value.spending_available !== false)
    || (value?.provider == null && value?.spending_available === true);
  return {
    billPaymentsAvailable: value?.bill_payments_available == null ? legacyAvailable : value.bill_payments_available === true,
    transfersAvailable: value?.transfers_available == null ? legacyAvailable : value.transfers_available === true,
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

export type VirtualAccount = ApiResult<CapabilityPayload & {
  has_account?: boolean;
  available?: boolean;
  enrollment_available?: boolean;
  enrollment_mode?: 'validation' | 'live';
  enrollment_status?: 'ready' | 'verification_required' | 'review_required' | 'not_available' | 'enrolled' | 'restricted';
  enrollment_blockers?: string[];
  enrollment_message?: string;
  re_registration_required?: boolean;
  consent_version?: string;
  migration_message?: string;
  validation_account_number?: string;
  validation_account_name?: string;
  account_setup_state?: string;
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

export const vasAccountStatusTitle = (value: VirtualAccount | null): string => {
  if (value?.account_setup_state === 'restricted' || value?.enrollment_status === 'restricted') return 'Account restricted';
  if (value?.account_setup_state === 'vas_validation') return 'Account activation pending';
  if (value?.enrollment_status === 'verification_required') return 'Verify your identity';
  if (value?.enrollment_status === 'review_required') return 'Account setup needs review';
  return 'Set up your funding account';
};

export type VasIdentityResult = VirtualAccount & {
  challenge_id?: string;
  identity_verified?: boolean;
  retry_available?: boolean;
  delivery_partial?: boolean;
  resend_after?: number;
};

export const walletService = {
  getBalance: () => apiJson<WalletBalance>(EP.wallet.balance),
  getHistory: () => apiJson<TransactionHistory>(EP.wallet.history),
  // Dedicated (virtual) account: fetch the existing one, or start BVN/NIN OTP provisioning.
  getAccount: () => apiJson<VirtualAccount>(EP.wallet.account),
  getVasStatus: () => apiJson<VirtualAccount>(EP.wallet.vasStatus),
  startVasIdentity: (kind: 'bvn' | 'nin', number: string,
    displayed: Pick<VirtualAccount, 'enrollment_mode' | 'consent_version'>) =>
    apiJson<VasIdentityResult>(EP.wallet.vasIdentityStart, {
      identity_type: kind, number, consent: true, enrollment_mode: displayed.enrollment_mode,
      consent_version: displayed.consent_version,
    }),
  confirmVasIdentity: (challengeId: string, otp?: string) =>
    apiJson<VasIdentityResult>(EP.wallet.vasIdentityConfirm, { challenge_id: challengeId, ...(otp ? { otp } : {}) }),
  resendVasIdentity: (challengeId: string) =>
    apiJson<VasIdentityResult>(EP.wallet.vasIdentityResend, { challenge_id: challengeId }),
  enrollVas: (identity: { bvn: string; nin?: never } | { nin: string; bvn?: never },
    displayed: Pick<VirtualAccount, 'enrollment_mode' | 'consent_version'> = {}) =>
    apiJson<VirtualAccount>(EP.wallet.vasEnroll, { ...identity, consent: true,
      ...(displayed.enrollment_mode ? { enrollment_mode: displayed.enrollment_mode } : {}),
      ...(displayed.consent_version ? { consent_version: displayed.consent_version } : {}),
    }),
  createAccount: (identity: { bvn?: string; nin?: string } | string) =>
    apiJson<VirtualAccount>(
      EP.wallet.createAccount,
      typeof identity === 'string' ? { bvn: identity } : identity,
    ),
  verifyWemaOtp: (trackingId: string, otp: string, identity: { bvn?: string; nin?: string } = {}) =>
    apiJson<VirtualAccount>(EP.wallet.wemaVerifyOtp, { tracking_id: trackingId, otp, ...identity }),
  resendWemaOtp: (trackingId: string) =>
    apiJson<VirtualAccount>(EP.wallet.wemaResendOtp, { tracking_id: trackingId }),
  // One request, or nothing: the bank scores BVN, NIN and the live image
  // together for an account it has already opened. `live_image` is base64 with
  // no data: prefix, matching kycService.verifyFace.
  upgradeTier2: (bvn: string, nin: string, liveImage: string) =>
    apiJson<VirtualAccount>(EP.wallet.wemaUpgradeTier2,
                            { bvn, nin, live_image: liveImage }),
};

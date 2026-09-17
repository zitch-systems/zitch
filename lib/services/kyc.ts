// KYC service — typed wrappers over the /api/kyc/* endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { walletService } from '@/lib/services/wallet';
import type { ApiResult } from '@/lib/services/types';

export type KycStatus = ApiResult<{
  tier: number;
  transaction_limit: string;
  bvn_verified: boolean;
  nin_verified: boolean;
  face_verified: boolean;
  pending?: boolean;
  identity_review_required?: boolean;
  otp_required?: boolean;
  tracking_id?: string;
  delivery?: string;
  otp_destination?: string;
  otp_destination_kind?: string;
  using_bvn?: boolean;
  account_setup_state?: 'ready' | 'otp_pending' | 'processing' | 'identity_verified' | 'identity_required';
  upgrade_required?: boolean;
  next_step?: string;
  address_verified?: boolean;
  identity_face_available?: boolean;
  identity_upgrade_required?: boolean;
  bank_upgrade_required?: boolean;
  bank_tier?: number;
  tier_name?: string;
  daily_transfer_limit?: string;
  daily_bill_limit?: string;
  id_document_verified?: boolean;
  face_rail?: 'document' | 'wema';
  tier2_face_rail?: 'prembly' | 'wema';
  address_rail?: 'document' | 'wema';
}>;

export type ResidentialAddress = {
  buildingNumber: string;
  apartment: string;
  street: string;
  city: string;
  town: string;
  state: string;
  lga: string;
  lcda: string;
  landmark: string;
  additionalInformation: string;
  country: string;
  fullAddress: string;
  postalCode: string;
};

export type KycVerificationFlag = 'bvn_verified' | 'nin_verified' | 'face_verified' | 'address_verified';
export type KycResponseKind = 'pending' | 'review' | 'unverified' | 'success' | 'error';
export type IdentityOtpKind = 'bvn' | 'nin';

export const isAccountOtpPending = (response: Pick<KycStatus, 'status' | 'account_setup_state'>): boolean =>
  typeof response.status === 'string'
    ? response.status === 'account_otp_pending'
    : response.account_setup_state === 'otp_pending';

/**
 * Resolve the bank OTP continuation returned by face-start. The tracking
 * reference belongs to the identity named by the server, so it takes
 * precedence over the screen that initiated the face request.
 */
export const resolveIdentityOtpRoute = (
  response: Pick<KycStatus, 'status' | 'account_setup_state' | 'tracking_id' | 'using_bvn' | 'otp_destination_kind'>,
  fallbackKind: IdentityOtpKind,
): { kind: IdentityOtpKind; trackingId: string } | null => {
  if (!isAccountOtpPending(response) || !response.tracking_id) return null;
  const kind = response.using_bvn === true || response.otp_destination_kind === 'bvn'
    ? 'bvn'
    : response.using_bvn === false || response.otp_destination_kind === 'nin'
      ? 'nin'
      : fallbackKind;
  return { kind, trackingId: String(response.tracking_id) };
};

export const classifyKycResponse = (response: {
  success?: boolean;
  pending?: boolean;
  identity_review_required?: boolean;
} & Partial<Record<KycVerificationFlag, boolean>>, requiredFlags: KycVerificationFlag[] = []): KycResponseKind => {
  if (response.pending) return 'pending';
  if (response.identity_review_required) return 'review';
  if (response.success && requiredFlags.length && !requiredFlags.every((flag) => response[flag] === true)) return 'unverified';
  return response.success ? 'success' : 'error';
};

export const kycService = {
  getStatus: () => apiJson<KycStatus>(EP.kyc.status),
  startBvn: (bvn: string) => apiJson<KycStatus>(EP.kyc.bvnStart, { bvn }),
  confirmBvn: (trackingId: string, otp: string) =>
    apiJson<KycStatus>(EP.kyc.bvnConfirm, { tracking_id: trackingId, otp }),
  resendBvn: (trackingId: string) => walletService.resendWemaOtp(trackingId),
  startNin: (nin: string) => walletService.createAccount({ nin }),
  confirmNin: (trackingId: string, otp: string, nin: string) =>
    walletService.verifyWemaOtp(trackingId, otp, { nin }),
  resendNin: (trackingId: string) => walletService.resendWemaOtp(trackingId),
  verifyNin: (nin: string, ninImage: string) => apiJson<KycStatus>(EP.kyc.nin, { nin, nin_image: ninImage }),
  verifyFace: (selfie: string) => apiJson<KycStatus>(EP.kyc.face, { selfie }),
  verifyAddress: (address: ResidentialAddress, document?: string) => {
    const fullAddress = address.fullAddress || [
      [address.buildingNumber, address.apartment, address.street].filter(Boolean).join(' '),
      address.city,
      address.state,
    ].filter(Boolean).join(', ');
    return apiJson<KycStatus>(EP.kyc.address, {
      residentialAddress: { ...address, country: address.country || 'Nigeria', fullAddress },
      ...(document ? { document } : {}),
    });
  },
  startIdentityFace: (identity: { bvn?: string; nin?: string }) =>
    apiJson<KycStatus & { url?: string; session?: string; expires_in?: number }>(
      EP.kyc.identityFaceStart,
      { ...identity, prefer_face: true },
    ),
  getIdentityFaceStatus: (session: string) =>
    apiJson<KycStatus & { status?: string }>(EP.kyc.identityFaceStatus, { session }),
  // The route out of `upgrade_required`. startNin/startBvn cannot finish an
  // account the bank has already opened — they come back with that flag and
  // nothing else will move the customer forward.
  upgradeTier2: (bvn: string, nin: string, liveImage: string) =>
    walletService.upgradeTier2(bvn, nin, liveImage),
};

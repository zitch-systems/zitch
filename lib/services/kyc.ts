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
}>;

export const kycService = {
  getStatus: () => apiJson<KycStatus>(EP.kyc.status),
  startBvn: (bvn: string) => apiJson<KycStatus>(EP.kyc.bvnStart, { bvn }),
  confirmBvn: (otp: string) => apiJson<KycStatus>(EP.kyc.bvnConfirm, { otp }),
  startNin: (nin: string) => walletService.createAccount({ nin }) as Promise<KycStatus & {
    otp_required?: boolean;
    tracking_id?: string;
    using_bvn?: boolean;
    otp_destination_kind?: string;
    // Set when the bank has already opened the account number and will no
    // longer take a lone identity. Not a retryable error: the caller has to
    // switch to the combined upgrade.
    upgrade_required?: boolean;
  }>,
  confirmNin: (trackingId: string, otp: string, nin: string) =>
    walletService.verifyWemaOtp(trackingId, otp, { nin }) as Promise<KycStatus>,
  verifyNin: (nin: string, ninImage: string) => apiJson<KycStatus>(EP.kyc.nin, { nin, nin_image: ninImage }),
  verifyFace: (selfie: string) => apiJson<KycStatus>(EP.kyc.face, { selfie }),
  // The route out of `upgrade_required`. startNin/startBvn cannot finish an
  // account the bank has already opened — they come back with that flag and
  // nothing else will move the customer forward.
  upgradeTier2: (bvn: string, nin: string, liveImage: string) =>
    walletService.upgradeTier2(bvn, nin, liveImage) as Promise<KycStatus>,
};

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
  }>,
  confirmNin: (trackingId: string, otp: string, nin: string) =>
    walletService.verifyWemaOtp(trackingId, otp, { nin }) as Promise<KycStatus>,
  verifyNin: (nin: string, ninImage: string) => apiJson<KycStatus>(EP.kyc.nin, { nin, nin_image: ninImage }),
  verifyFace: (selfie: string) => apiJson<KycStatus>(EP.kyc.face, { selfie }),
};

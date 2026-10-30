// Identity and upgrades use the 9PSB banking service. This read remains for the profile screen.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

export type KycStatus = ApiResult<{
  tier: number; bank_tier?: number; bvn_verified: boolean; nin_verified: boolean;
  face_verified: boolean; address_verified?: boolean; identity_verification_provider?: '9psb';
  account_provider?: '9psb'; address_rail?: 'prembly';
}>;
export const kycService = {
  getStatus: () => apiJson<KycStatus>(EP.kyc.status),
  startEmail: (email: string) => apiJson<KycStatus>(EP.auth.emailVerifyStart, { email }),
  confirmEmail: (otp: string) => apiJson<KycStatus>(EP.auth.emailVerifyConfirm, { otp }),
};

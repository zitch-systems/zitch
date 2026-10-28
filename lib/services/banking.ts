import { apiJson } from '@/lib/api';
import type { ApiResult } from './types';

export type BankProfile = {
  lastName: string; otherNames: string; phoneNo: string; email?: string;
  gender?: number; dateOfBirth: string; address: string; ninUserId?: string;
};
export type BankStatus = ApiResult<{
  provider: '9psb'; bank_tier: number; tier: number; sandbox: boolean;
  bvn_verified: boolean; nin_verified: boolean; face_verified: boolean; address_verified: boolean;
  profile: BankProfile; account_number?: string; account_name?: string;
  identity_available?: boolean; address_verification_available?: boolean;
  email_verified?: boolean; phone_verified?: boolean; email_verification_required?: boolean;
  can_fund?: boolean; available?: boolean; has_account?: boolean;
  legacy_funding_review?: boolean; migration_message?: string;
  identity_challenge?: BankProgress & { kind: 'bvn' | 'nin' };
  account_setup_state?: string; maximum_single_deposit?: string | null; maximum_balance?: string | null;
}>;
export type BankProgress = ApiResult<{
  tracking_id?: string; otp_required?: boolean; account_setup_state?: string;
  expires_at?: string; state?: string;
}>;
export type CatalogItem = { id?: string; name?: string; productId?: string; amount?: string | number;
  dataBundle?: string; validity?: string; fieldName?: string; fieldDescription?: string;
  items?: { itemId: string; itemName: string; amount: string | number }[] };

export const banking = {
  status: () => apiJson<BankStatus>('/api/banking/status/'),
  identity: (kind: 'bvn' | 'nin', identifier: string, profile: BankProfile, image?: string) =>
    apiJson<BankProgress>('/api/banking/identity/start/', { kind, identifier, profile,
      method: image ? 'FACIAL' : 'OTP', ...(image ? { image } : {}) }),
  confirm: (reference: string, otp: string) => apiJson<BankProgress>('/api/banking/identity/confirm/', { reference, otp }),
  progress: (reference: string) => apiJson<BankProgress>('/api/banking/identity/status/', { reference }),
  upgrade: (tier: number, profile: Record<string, unknown>, address_reference?: string) =>
    apiJson<BankProgress>('/api/banking/upgrade/', { tier, profile, address_reference }),
  address: (address: Record<string, string>) => apiJson<BankProgress>('/api/banking/address/start/', { address }),
  addressStatus: (reference: string) => apiJson<BankProgress>('/api/banking/address/status/', { reference }),
  catalog: (kind: string, params: Record<string, string> = {}) =>
    apiJson<ApiResult<{ data: CatalogItem[] }>>('/api/banking/catalog/', { kind, ...params }),
  validate: (body: Record<string, unknown>) =>
    apiJson<ApiResult<{ validation_reference?: string; customer_name?: string; amount?: string }>>('/api/banking/bills/validate/', body),
  purchase: (body: Record<string, unknown>) => apiJson<ApiResult>('/api/banking/purchase/', body),
};

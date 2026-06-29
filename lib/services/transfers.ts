// Transfers service — typed wrappers over the /api/transfers/* endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

export type ResolveResult = ApiResult<{
  account_name?: string;
  bank_name?: string;
  bank_code?: string;
  matches?: { account_name: string; bank_name: string; bank_code: string }[];
}>;

export type SendResult = ApiResult<{ reference?: string }>;

export type Beneficiary = {
  id: string;
  account_name: string;
  account_number: string;
  bank_name: string;
  bank_code?: string;
};

export const transfersService = {
  resolve: (accountNumber: string, bankCode?: string) =>
    apiJson<ResolveResult>(EP.transfers.resolve, { account_number: accountNumber, bank_code: bankCode }),
  send: (body: Record<string, any>, idempotencyKey: string) =>
    apiJson<SendResult>(EP.transfers.send, { ...body, idempotency_key: idempotencyKey }),
  getBeneficiaries: () => apiJson<ApiResult<{ beneficiaries?: Beneficiary[] }>>(EP.transfers.beneficiaries),
};

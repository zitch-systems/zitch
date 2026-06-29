// Loans service — typed wrappers over the /api/loans/* endpoints.
// NOTE: getloan.tsx reads loan status via apiPost (it branches on response.ok),
// so only the apiJson call sites are wrapped here.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type LoanResult = ApiResult<Record<string, any>>;

export const loansService = {
  getStatus: () => apiJson<LoanResult>(EP.loans.status),
  request: (amount: number | string, tenureDays: number | string, pin: string) =>
    apiJson<LoanResult>(EP.loans.request, { amount: String(amount), tenure_days: tenureDays, transaction_pin: pin }),
  repay: (amount: number | string, pin: string, idempotencyKey: string) =>
    apiJson<LoanResult>(EP.loans.repay, { amount, transaction_pin: pin, idempotency_key: idempotencyKey }),
};

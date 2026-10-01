// Savings (fixed save) service — typed wrappers over /api/savings/*.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

export type SavingsResult = ApiResult<{
  product_available?: boolean;
  unavailable_message?: string;
  rates?: { days: number | string; rate: number | string }[];
  min?: number | string;
  plans?: any[];
  total_locked?: number | string;
}>;

export const savingsService = {
  getRates: () => apiJson<SavingsResult>(EP.savings.rates),
  list: () => apiJson<SavingsResult>(EP.savings.list),
  create: (amount: number | string, days: number, pin: string, idempotencyKey: string) =>
    apiJson<SavingsResult>(EP.savings.create, { amount, days, transaction_pin: pin, idempotency_key: idempotencyKey }),
};

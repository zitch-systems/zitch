// Savings (fixed save) service — typed wrappers over /api/savings/*.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type SavingsResult = ApiResult<Record<string, any>>;

export const savingsService = {
  list: () => apiJson<SavingsResult>(EP.savings.list),
  create: (amount: number | string, days: number, pin: string, idempotencyKey: string) =>
    apiJson<SavingsResult>(EP.savings.create, { amount, days, transaction_pin: pin, idempotency_key: idempotencyKey }),
};

// Bill verticals that use apiJson (betting top-up, exam pins, FX rate). The
// airtime/data/cable/electricity flows branch on response.ok via apiPost and are
// intentionally left on their screens.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type BillResult = ApiResult<Record<string, any>>;

export const bettingService = {
  fund: (platform: string, userId: string, amount: number | string, pin: string, idempotencyKey: string) =>
    apiJson<BillResult>(EP.betting.fund, {
      platform, user_id: userId, amount, transaction_pin: pin, idempotency_key: idempotencyKey,
    }),
};

export const examsService = {
  buy: (exam: string, quantity: number, phone: string, pin: string, idempotencyKey: string) =>
    apiJson<BillResult>(EP.exams.buy, {
      exam, quantity, phone, transaction_pin: pin, idempotency_key: idempotencyKey,
    }),
};

export const convertService = {
  getRate: () => apiJson<BillResult>(EP.convert.fx),
};

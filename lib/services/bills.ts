// Bill verticals that use apiJson (betting top-up, exam pins, FX rate). The
// airtime/data/cable/electricity flows branch on response.ok via apiPost and are
// intentionally left on their screens.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

type BillResult = ApiResult<Record<string, any>>;

export type RecentPhone = { phone: string; network: string; last_used: string };
export type RecentMeter = { meter: string; disco: string; meter_type: 'prepaid' | 'postpaid'; name: string; address: string; last_used: string };
export type RecentSmartcard = { iuc: string; provider: string; name: string; last_used: string };
export type RecentBetting = { platform: string; user_id: string; last_used: string };
export type RecentExamPhone = { phone: string; last_used: string };
export type BillRecents = {
  phones: RecentPhone[];
  meters: RecentMeter[];
  smartcards: RecentSmartcard[];
  betting: RecentBetting[];
  exam_phones: RecentExamPhone[];
};

export const recentsService = {
  get: () => apiJson<ApiResult<Partial<BillRecents>>>(EP.utility.recents),
};

export const bettingService = {
  // The name on the betting account, when the platform can be checked.
  verify: (platform: string, userId: string) =>
    apiJson<ApiResult<{ account_name?: string }>>(EP.betting.verify, { platform, user_id: userId }),
  fund: (platform: string, userId: string, amount: number | string, pin: string, idempotencyKey: string) =>
    apiJson<BillResult>(EP.betting.fund, {
      platform, user_id: userId, amount, transaction_pin: pin, idempotency_key: idempotencyKey,
    }),
};

export const examsService = {
  buy: (exam: string, quantity: number, phone: string, pin: string, idempotencyKey: string, expectedAmount?: number | string) =>
    apiJson<BillResult>(EP.exams.buy, {
      exam, quantity, phone, transaction_pin: pin, idempotency_key: idempotencyKey,
      ...(expectedAmount == null ? {} : { expected_amount: expectedAmount }),
    }),
};

export const convertService = {
  getRate: () => apiJson<BillResult>(EP.convert.fx),
};

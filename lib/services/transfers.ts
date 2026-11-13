// Transfers service — typed wrappers over the /api/transfers/* endpoints.
// Body shapes mirror sendmoney.tsx exactly (the resolve key is `bank`, not
// `bank_code`; omit it to let the backend auto-detect). The singular legacy
// paths are kept until the backend reconciles transfer vs transfers.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

export type ResolveResult = ApiResult<{
  // Resolved account-holder name (single-bank / legacy resolve paths).
  name: string;
  mock?: boolean;
  account_name?: string;
  bank_name?: string;
  bank_code?: string;
  phone?: string;
  /** Opaque, immutable account identifier used only to bind durable retries. */
  recipient_key?: string;
  // Auto-detect (no bank) returns candidate matches keyed by bank code.
  matches?: { bank: string; bank_name: string; name: string }[];
}>;

export type SendResult = ApiResult<{
  reference?: string;
  under_review?: boolean;
  review_kind?: string;
  status_message?: string;
  /** The bank's transfer fee debited with this transfer, as a decimal string. */
  fee?: string;
}>;

/** The bank's fee for sending `amount` to `bank`, and the total that will leave the wallet. */
export type ChargeResult = ApiResult<{ amount: string; fee: string; total: string }>;

export type SendBody = {
  account_number: string;
  bank?: string;
  name?: string;
  amount: number | string;
  transaction_pin: string;
  note?: string;
  idempotency_key: string;
};

export type SendLegacyBody = {
  identifier: string;
  recipient_key?: string;
  amount: number | string;
  transaction_pin: string;
  note?: string;
  idempotency_key: string;
};

export const transfersService = {
  // Account resolution; pass a bank code to scope it, omit to auto-detect.
  resolve: (accountNumber: string, bank?: string) =>
    apiJson<ResolveResult>(
      EP.transfers.resolve,
      bank ? { account_number: accountNumber, bank } : { account_number: accountNumber },
    ),
  resolveLegacy: (identifier: string) => apiJson<ResolveResult>(EP.transfers.resolveLegacy, { identifier }),
  // The fee quote shown before the customer confirms; `bank` is the same code `send` takes.
  charge: (amount: number | string, bank?: string) =>
    apiJson<ChargeResult>(EP.transfers.charge, bank ? { amount, bank } : { amount }),
  send: (body: SendBody) => apiJson<SendResult>(EP.transfers.send, body),
  sendLegacy: (body: SendLegacyBody) => apiJson<SendResult>(EP.transfers.sendLegacy, body),
};

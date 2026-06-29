// Cards service — typed wrappers over the /api/cards/* endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

// Endpoints return varied payloads (card lists, masked details, balances); the
// envelope (success/message) is what call sites branch on, so keep the extra
// fields open rather than over-constraining each screen's reads.
type CardResult = ApiResult<Record<string, any>>;

export const cardsService = {
  list: () => apiJson<CardResult>(EP.cards.list),
  create: () => apiJson<CardResult>(EP.cards.create),
  freeze: (cardId: number | string) => apiJson<CardResult>(EP.cards.freeze, { card_id: cardId }),
  fund: (cardId: number | string, amount: number | string, pin: string, idempotencyKey: string) =>
    apiJson<CardResult>(EP.cards.fund, { card_id: cardId, amount, transaction_pin: pin, idempotency_key: idempotencyKey }),
  details: (cardId: number | string, pin: string) =>
    apiJson<CardResult>(EP.cards.details, { card_id: cardId, transaction_pin: pin }),
};

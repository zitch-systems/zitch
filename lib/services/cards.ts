// Cards service — typed wrappers over the /api/cards/* endpoints.
import { apiJson } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import type { ApiResult } from '@/lib/services/types';

export type CardCapabilities = {
  can_fund: boolean;
  can_unfreeze: boolean;
  permanent_block: boolean;
};

export type VirtualCard = {
  id: number;
  brand: string;
  last4: string;
  masked: string;
  expiry: string;
  holder: string;
  balance: string;
  status: string;
  frozen: boolean;
  capabilities: CardCapabilities;
};

export type CardResult = ApiResult<{
  card?: VirtualCard;
  cards?: VirtualCard[];
  issuance?: { pending?: boolean; reference?: string; status?: string } | null;
  wallet?: string;
  pan?: string;
  cvv?: string;
  expiry?: string;
  holder?: string;
  _httpOk?: boolean;
  _httpStatus?: number;
}>;

export const cardsService = {
  list: () => apiJson<CardResult>(EP.cards.list),
  create: (idempotencyKey: string) =>
    apiJson<CardResult>(EP.cards.create, { idempotency_key: idempotencyKey }),
  freeze: (cardId: number | string) => apiJson<CardResult>(EP.cards.freeze, { card_id: cardId }),
  fund: (cardId: number | string, amount: number | string, pin: string, idempotencyKey: string) =>
    apiJson<CardResult>(EP.cards.fund, { card_id: cardId, amount, transaction_pin: pin, idempotency_key: idempotencyKey }),
  details: (cardId: number | string, pin: string) =>
    apiJson<CardResult>(EP.cards.details, { card_id: cardId, transaction_pin: pin }),
};

/**
 * Classify the response to a money-moving request.
 *
 * A duplicate/idempotency flag is deliberately irrelevant here: it says the
 * server reused an earlier attempt, not whether that attempt succeeded. Pending
 * always wins if a malformed or legacy response carries both flags. For callers
 * that still use apiPost, a 2xx body without an explicit outcome is ambiguous,
 * not successful; keeping it `unknown` makes the caller retain the attempt key.
 */
export type SpendOutcome = 'success' | 'pending' | 'failed' | 'unknown';

export type SpendResponse = {
  success?: unknown;
  pending?: unknown;
  duplicate?: unknown;
  offline?: unknown;
  code?: unknown;
  _httpOk?: unknown;
  _httpStatus?: unknown;
};

// These responses are produced before a money handler can debit or call its
// provider. Their HTTP 429 is therefore a definitive refusal, not an ambiguous
// delivery outcome. Keep the list narrow: an unrecognised/gateway 429 still
// retains the durable attempt key below.
const PRE_EXECUTION_FAILURE_CODES = new Set([
  'pin_locked',
  'velocity',
  'rate_limited',
]);

export function classifySpendResponse(
  response: SpendResponse | null | undefined,
  transport?: boolean | number,
): SpendOutcome {
  const status = typeof transport === 'number'
    ? transport
    : (typeof response?._httpStatus === 'number' ? response._httpStatus : undefined);
  const transportOk = typeof transport === 'boolean'
    ? transport
    : (typeof status === 'number'
      ? status >= 200 && status < 300
      : (typeof response?._httpOk === 'boolean' ? response._httpOk : undefined));
  const ambiguousStatus = status === 408
    || status === 425
    || status === 429
    || (typeof status === 'number' && status >= 500);
  if (response?.pending === true) return 'pending';
  if ((status === undefined || status === 429)
      && typeof response?.code === 'string'
      && PRE_EXECUTION_FAILURE_CODES.has(response.code)) return 'failed';
  // Transport evidence wins over a contradictory body. A proxy/gateway can
  // return a stale or malformed JSON envelope, and a transient HTTP response is
  // never proof that the money operation committed — even when it says
  // success=true. Retain the durable key so the next attempt can replay it.
  if (response?.offline === true || ambiguousStatus) return 'unknown';
  if (response?.success === true && transportOk !== false) return 'success';
  if (transportOk === true) return 'unknown';
  return 'failed';
}

/** A terminal replay confirms the earlier authorization; it is not a new sale. */
export function isRecoveredSpendResponse(
  response: SpendResponse | null | undefined,
  transport?: boolean | number,
): boolean {
  return response?.duplicate === true
    && classifySpendResponse(response, transport) === 'success';
}

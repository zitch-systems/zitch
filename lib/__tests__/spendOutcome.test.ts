import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';

describe('classifySpendResponse', () => {
  it('gives pending precedence over success and duplicate metadata', () => {
    expect(classifySpendResponse({ success: true, pending: true })).toBe('pending');
    expect(classifySpendResponse({ pending: true, success: false })).toBe('pending');
  });

  it('requires an explicit success outcome', () => {
    expect(classifySpendResponse({ success: true }, true)).toBe('success');
    expect(classifySpendResponse({ success: true }, false)).toBe('failed');
    expect(classifySpendResponse({}, true)).toBe('unknown');
    expect(classifySpendResponse({ _httpOk: true })).toBe('unknown');
  });

  it('keeps transport failures unresolved so callers retain their key', () => {
    expect(classifySpendResponse({ success: false, offline: true })).toBe('unknown');
  });

  it.each([408, 425, 429, 500, 502, 503, 599])(
    'retains the attempt for ambiguous HTTP %s even with structured failure JSON',
    (status) => {
      expect(classifySpendResponse({ success: false }, status)).toBe('unknown');
    },
  );

  it.each([408, 425, 429, 500, 502, 503, 599])(
    'does not let success=true override ambiguous HTTP %s',
    (status) => {
      expect(classifySpendResponse({ success: true }, status)).toBe('unknown');
    },
  );

  it('honours apiJson transport metadata when no explicit status argument is passed', () => {
    expect(classifySpendResponse({ success: true, _httpOk: false, _httpStatus: 503 }))
      .toBe('unknown');
    expect(isRecoveredSpendResponse({
      success: true,
      duplicate: true,
      _httpOk: false,
      _httpStatus: 503,
    })).toBe(false);
  });

  it.each(['pin_locked', 'velocity', 'rate_limited'])(
    'treats known pre-execution rejection %s as failed even on HTTP 429',
    (code) => {
      expect(classifySpendResponse({ success: false, code }, 429)).toBe('failed');
      expect(classifySpendResponse({ success: true, code }, 429)).toBe('failed');
      expect(classifySpendResponse({
        success: false,
        code,
        _httpOk: false,
        _httpStatus: 429,
      })).toBe('failed');
    },
  );

  it.each([408, 425, 500, 502, 503])(
    'does not trust a stale pre-execution code on ambiguous HTTP %s',
    (status) => {
      expect(classifySpendResponse({ success: false, code: 'pin_locked' }, status))
        .toBe('unknown');
    },
  );

  it('treats a definitive non-success response as failed', () => {
    expect(classifySpendResponse({}, false)).toBe('failed');
    expect(classifySpendResponse({})).toBe('failed');
  });

  it('distinguishes a confirmed earlier attempt from a new success', () => {
    expect(isRecoveredSpendResponse({ success: true, duplicate: true }, true)).toBe(true);
    expect(isRecoveredSpendResponse({ success: true }, true)).toBe(false);
    expect(isRecoveredSpendResponse({ pending: true, duplicate: true }, true)).toBe(false);
  });
});

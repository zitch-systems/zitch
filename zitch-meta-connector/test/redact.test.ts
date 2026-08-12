import { afterEach, describe, expect, it } from 'vitest';

import { _resetRedactionForTests, redactDeep, registerSecret } from '../src/redact.js';

describe('redactDeep', () => {
  afterEach(() => {
    _resetRedactionForTests();
  });

  it('redacts a registered secret from a plain string', () => {
    registerSecret('super-secret-token');
    expect(redactDeep('the token is super-secret-token here')).toBe(
      'the token is [REDACTED] here',
    );
  });

  it('redacts a secret nested inside an object', () => {
    registerSecret('super-secret-token');
    const input = { a: { b: ['super-secret-token', 'fine'] } };
    expect(redactDeep(input)).toEqual({ a: { b: ['[REDACTED]', 'fine'] } });
  });

  it('redacts every occurrence, not just the first', () => {
    registerSecret('xx-secret-xx');
    expect(redactDeep('xx-secret-xx and again xx-secret-xx')).toBe(
      '[REDACTED] and again [REDACTED]',
    );
  });

  it('leaves unrelated content untouched', () => {
    registerSecret('super-secret-token');
    expect(redactDeep('nothing sensitive here')).toBe('nothing sensitive here');
  });

  it('ignores short values so it never redacts trivial substrings', () => {
    registerSecret('abc');
    expect(redactDeep('abcdef')).toBe('abcdef');
  });

  it('is a no-op with nothing registered', () => {
    expect(redactDeep('anything at all')).toBe('anything at all');
  });
});

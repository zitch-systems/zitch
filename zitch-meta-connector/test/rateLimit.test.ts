import { describe, expect, it } from 'vitest';

import { RateLimiter } from '../src/rateLimit.js';

describe('RateLimiter', () => {
  it('allows up to the configured max within a window', () => {
    const limiter = new RateLimiter(60_000, 3);
    const now = 1_000_000;
    expect(limiter.consume('a', now).allowed).toBe(true);
    expect(limiter.consume('a', now).allowed).toBe(true);
    expect(limiter.consume('a', now).allowed).toBe(true);
    expect(limiter.consume('a', now).allowed).toBe(false);
  });

  it('tracks distinct keys independently', () => {
    const limiter = new RateLimiter(60_000, 1);
    const now = 1_000_000;
    expect(limiter.consume('a', now).allowed).toBe(true);
    expect(limiter.consume('b', now).allowed).toBe(true);
    expect(limiter.consume('a', now).allowed).toBe(false);
    expect(limiter.consume('b', now).allowed).toBe(false);
  });

  it('resets after the window elapses', () => {
    const limiter = new RateLimiter(1000, 1);
    const now = 1_000_000;
    expect(limiter.consume('a', now).allowed).toBe(true);
    expect(limiter.consume('a', now + 500).allowed).toBe(false);
    expect(limiter.consume('a', now + 1001).allowed).toBe(true);
  });

  it('reports a sane retry-after when over budget', () => {
    const limiter = new RateLimiter(1000, 1);
    const now = 1_000_000;
    limiter.consume('a', now);
    const second = limiter.consume('a', now + 200);
    expect(second.allowed).toBe(false);
    expect(second.retryAfterMs).toBeGreaterThan(0);
    expect(second.retryAfterMs).toBeLessThanOrEqual(1000);
  });

  it('sweep() removes expired windows but keeps live ones', () => {
    const limiter = new RateLimiter(1000, 5);
    const now = 1_000_000;
    limiter.consume('expired', now);
    limiter.consume('live', now + 2000);
    expect(limiter.size).toBe(2);
    limiter.sweep(now + 2000);
    expect(limiter.size).toBe(1);
  });
});

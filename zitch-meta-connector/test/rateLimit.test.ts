import { describe, expect, it, vi } from 'vitest';

import { RateLimiter, ipRateLimitMiddleware } from '../src/rateLimit.js';

function fakeReqRes(ip: string) {
  const json = vi.fn();
  const status = vi.fn(() => ({ json }));
  const req = { ip } as any;
  const res = { status } as any;
  const next = vi.fn();
  return { req, res, next, status, json };
}

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

describe('ipRateLimitMiddleware', () => {
  // This is the pre-auth stage — it must throttle a caller regardless of
  // whether they ever present a valid key, so no test here goes anywhere
  // near auth.ts; keying is by IP alone.

  it('lets requests through under budget', () => {
    const limiter = new RateLimiter(60_000, 2);
    const mw = ipRateLimitMiddleware(limiter);
    const { req, res, next, status } = fakeReqRes('10.0.0.1');
    mw(req, res, next);
    expect(next).toHaveBeenCalledOnce();
    expect(status).not.toHaveBeenCalled();
  });

  it('blocks with 429 once an IP is over budget, auth never having been checked', () => {
    const limiter = new RateLimiter(60_000, 1);
    const mw = ipRateLimitMiddleware(limiter);

    const first = fakeReqRes('10.0.0.2');
    mw(first.req, first.res, first.next);
    expect(first.next).toHaveBeenCalledOnce();

    const second = fakeReqRes('10.0.0.2');
    mw(second.req, second.res, second.next);
    expect(second.next).not.toHaveBeenCalled();
    expect(second.status).toHaveBeenCalledWith(429);
    expect(second.json).toHaveBeenCalledWith(
      expect.objectContaining({ error: 'rate_limited' }),
    );
  });

  it('throttles every request from an IP, independent of whatever key (or no key) each one presents', () => {
    // Simulates a wrong-key-guessing flood: every call is a DISTINCT fake
    // "key" but the SAME source IP, and the limiter never looks at the key at
    // all — only req.ip — so the budget is shared across all of them.
    const limiter = new RateLimiter(60_000, 3);
    const mw = ipRateLimitMiddleware(limiter);
    const results: boolean[] = [];
    for (let i = 0; i < 5; i += 1) {
      const { req, res, next } = fakeReqRes('10.0.0.3');
      mw(req, res, next);
      results.push(next.mock.calls.length > 0);
    }
    expect(results).toEqual([true, true, true, false, false]);
  });

  it('keeps distinct IPs on independent budgets', () => {
    const limiter = new RateLimiter(60_000, 1);
    const mw = ipRateLimitMiddleware(limiter);
    const a = fakeReqRes('10.0.0.4');
    const b = fakeReqRes('10.0.0.5');
    mw(a.req, a.res, a.next);
    mw(b.req, b.res, b.next);
    expect(a.next).toHaveBeenCalledOnce();
    expect(b.next).toHaveBeenCalledOnce();
  });
});

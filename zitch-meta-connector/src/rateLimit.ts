/**
 * Fixed-window rate limiter. Applied in TWO places, deliberately in this
 * order (see index.ts):
 *
 *   1. `ipRateLimitMiddleware`, keyed by remote IP, runs BEFORE auth. A
 *      request that fails authentication is rejected before it ever reaches
 *      the API-key check — which means it never gets a `keyFingerprint`, and
 *      would sail straight past a limiter that only looked at that. Without
 *      this pre-auth stage, unlimited wrong-key or no-key requests could be
 *      thrown at the service all day with no throttling at all: cheap
 *      per-request (a JSON parse and a constant-time compare), but free
 *      volume is still an open brute-force/DoS door on a service whose only
 *      access control IS that one key. This closes it, regardless of whether
 *      the caller ever presents a valid key.
 *   2. `rateLimitMiddleware`, keyed by the authenticated key's fingerprint,
 *      runs AFTER auth succeeds — the original, finer-grained per-caller
 *      budget for legitimate traffic.
 *
 * In-memory and per-process by design: this connector is meant to run as a
 * single small instance in front of a low-volume admin/maintenance workload,
 * not as a horizontally-scaled public API. If it's ever scaled out, swap this
 * for a shared store (Redis) — the interface below is deliberately narrow so
 * that swap only touches this file.
 */
import type { NextFunction, Request, Response } from 'express';

import type { Config } from './config.js';

interface Window {
  count: number;
  resetAt: number;
}

export class RateLimiter {
  private readonly windows = new Map<string, Window>();

  constructor(
    private readonly windowMs: number,
    private readonly maxRequests: number,
  ) {}

  /** Returns true if the request is allowed, false if the caller is over budget. */
  consume(key: string, now: number = Date.now()): { allowed: boolean; retryAfterMs: number } {
    let window = this.windows.get(key);
    if (!window || window.resetAt <= now) {
      window = { count: 0, resetAt: now + this.windowMs };
      this.windows.set(key, window);
    }
    window.count += 1;
    if (window.count > this.maxRequests) {
      return { allowed: false, retryAfterMs: window.resetAt - now };
    }
    return { allowed: true, retryAfterMs: 0 };
  }

  /** Periodic cleanup so the map doesn't grow unbounded across many distinct callers. */
  sweep(now: number = Date.now()): void {
    for (const [key, window] of this.windows) {
      if (window.resetAt <= now) this.windows.delete(key);
    }
  }

  get size(): number {
    return this.windows.size;
  }
}

function respondRateLimited(res: Response, retryAfterMs: number): void {
  res.status(429).json({
    error: 'rate_limited',
    message: `Too many requests. Retry after ${Math.ceil(retryAfterMs / 1000)}s.`,
  });
}

/** Post-auth limiter: keyed by the authenticated caller's key fingerprint. */
export function rateLimitMiddleware(_config: Config, limiter: RateLimiter) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const key = (res.locals.keyFingerprint as string | undefined) || req.ip || 'unknown';
    const result = limiter.consume(key);
    if (!result.allowed) {
      respondRateLimited(res, result.retryAfterMs);
      return;
    }
    next();
  };
}

/** Pre-auth limiter: keyed by remote IP alone, since no key has been
 * validated (or even necessarily presented) yet at this point in the chain.
 * Must run before `requireApiKey` — see the module comment above. */
export function ipRateLimitMiddleware(limiter: RateLimiter) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const result = limiter.consume(req.ip || 'unknown');
    if (!result.allowed) {
      respondRateLimited(res, result.retryAfterMs);
      return;
    }
    next();
  };
}

/** Starts a background sweep so idle callers' windows get garbage collected.
 * Returns a stop function; call it on shutdown so the process can exit cleanly. */
export function startRateLimiterSweep(limiter: RateLimiter, intervalMs = 60_000): () => void {
  const timer = setInterval(() => limiter.sweep(), intervalMs);
  timer.unref();
  return () => clearInterval(timer);
}

/**
 * Fixed-window rate limiter, keyed by the caller's key fingerprint (falling
 * back to remote IP for unauthenticated requests, so a flood of bad-auth
 * requests can't dodge the limiter by omitting a key).
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

export function rateLimitMiddleware(config: Config, limiter: RateLimiter) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const key = (res.locals.keyFingerprint as string | undefined) || req.ip || 'unknown';
    const result = limiter.consume(key);
    if (!result.allowed) {
      res.status(429).json({
        error: 'rate_limited',
        message: `Too many requests. Retry after ${Math.ceil(result.retryAfterMs / 1000)}s.`,
      });
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

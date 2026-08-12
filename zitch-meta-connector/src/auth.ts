/**
 * Per-request authentication.
 *
 * Every request — not just the first one on a connection — must carry a valid
 * CONNECTOR_API_KEY. There is no session, no cookie, no "authenticate once and
 * reuse a token": the MCP Streamable HTTP transport can span many independent
 * HTTP requests, and each one is checked here before it reaches the transport
 * or any tool handler.
 *
 * Accepts the key as `Authorization: Bearer <key>` (preferred) or the
 * `X-Connector-Api-Key` header (for clients — like a simple curl check or a
 * future GPT Action — that don't want to construct a Bearer header).
 */
import { timingSafeEqual } from 'node:crypto';
import type { NextFunction, Request, Response } from 'express';

import type { Config } from './config.js';

function extractKey(req: Request): string | undefined {
  const auth = req.header('authorization');
  if (auth?.toLowerCase().startsWith('bearer ')) {
    return auth.slice(7).trim();
  }
  const header = req.header('x-connector-api-key');
  return header?.trim() || undefined;
}

/** Constant-time string comparison — a plain `===` leaks timing information
 * proportional to the matching prefix length, which is a real side channel
 * against a secret this short-lived and this replayable. */
function safeEqual(a: string, b: string): boolean {
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) {
    // Compare against itself so the failure path still takes the same time
    // as a same-length mismatch, rather than short-circuiting on length.
    timingSafeEqual(bufA, bufA);
    return false;
  }
  return timingSafeEqual(bufA, bufB);
}

export interface AuthResult {
  ok: boolean;
  /** First 6 chars of the presented key, for audit logs — never the full key. */
  keyFingerprint: string;
}

export function checkAuth(req: Request, config: Config): AuthResult {
  const presented = extractKey(req);
  if (!presented) {
    return { ok: false, keyFingerprint: '(none)' };
  }
  const fingerprint = presented.slice(0, 6);
  return { ok: safeEqual(presented, config.connectorApiKey), keyFingerprint: fingerprint };
}

/** Express middleware form; stores the auth result on `res.locals` for the
 * audit logger, and short-circuits with 401 on failure. */
export function requireApiKey(config: Config) {
  return (req: Request, res: Response, next: NextFunction): void => {
    const result = checkAuth(req, config);
    res.locals.keyFingerprint = result.keyFingerprint;
    if (!result.ok) {
      res.status(401).json({
        error: 'unauthorized',
        message: 'Missing or invalid CONNECTOR_API_KEY.',
      });
      return;
    }
    next();
  };
}

/**
 * Entry point: an Express app exposing
 *   POST /mcp        — the MCP Streamable HTTP endpoint (for Claude / any MCP client)
 *   GET  /rest/*      — a plain REST mirror of the same tools (for a future GPT Action)
 *   GET  /healthz     — unauthenticated liveness probe (no config/secrets in the response)
 *
 * Every request to /mcp and /rest/* passes through, in order: JSON body
 * limits -> per-IP rate limiting (pre-auth) -> API key auth -> per-key rate
 * limiting (post-auth) -> audit-logged tool execution. The IP-keyed stage
 * runs BEFORE auth specifically so a flood of wrong/missing-key requests
 * gets throttled too, not just successfully authenticated traffic — see the
 * module comment in rateLimit.ts. auth.ts, rateLimit.ts, audit.ts cover each
 * stage in detail.
 */
import { randomUUID } from 'node:crypto';

import express from 'express';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';

import { loadConfig } from './config.js';
import { registerSecret } from './redact.js';
import { requireApiKey } from './auth.js';
import {
  RateLimiter,
  ipRateLimitMiddleware,
  rateLimitMiddleware,
  startRateLimiterSweep,
} from './rateLimit.js';
import { buildMcpServer } from './mcpServer.js';
import { buildRestRouter } from './rest.js';

const config = loadConfig();

// Defense in depth (see redact.ts) — the token must never appear in a
// response body, log line, or error message, even by accident.
registerSecret(config.metaAccessToken);
registerSecret(config.connectorApiKey);
if (config.metaAppSecret) registerSecret(config.metaAppSecret);

const app = express();
app.disable('x-powered-by');
app.use((_req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Cache-Control', 'no-store');
  next();
});
// Small limit: every request body here is a JSON-RPC tool call with a
// handful of short string/number fields — nothing this server does needs a
// large payload, so capping it removes an easy memory-exhaustion vector.
app.use(express.json({ limit: '256kb' }));

app.get('/healthz', (_req, res) => {
  res.status(200).json({
    status: 'ok',
    service: 'zitch-meta-connector',
    readOnly: config.readOnly,
    toolsExposed: 6,
  });
});

const limiter = new RateLimiter(config.rateLimitWindowMs, config.rateLimitMaxRequests);
startRateLimiterSweep(limiter);

// Separate limiter/budget so a burst of bad-auth traffic and a legitimate
// authenticated caller's own budget can never share (and drain) one bucket.
const ipLimiter = new RateLimiter(config.rateLimitWindowMs, config.ipRateLimitMaxRequests);
startRateLimiterSweep(ipLimiter);

const authenticated = [
  ipRateLimitMiddleware(ipLimiter),
  requireApiKey(config),
  rateLimitMiddleware(config, limiter),
];

app.post('/mcp', ...authenticated, async (req, res) => {
  const requestMeta = {
    keyFingerprint: (res.locals.keyFingerprint as string | undefined) ?? '(none)',
    ip: req.ip,
  };
  const server = buildMcpServer(config, requestMeta);
  try {
    // Stateless: a fresh transport (no sessionIdGenerator) per request, so
    // there is no session store to secure, expire, or leak across callers.
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on('close', () => {
      transport.close();
      server.close();
    });
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch {
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: '2.0',
        error: { code: -32603, message: 'Internal server error' },
        id: null,
      });
    }
  }
});

// The Streamable HTTP transport also defines GET (server-initiated stream)
// and DELETE (session teardown) — neither applies in stateless mode.
app.get('/mcp', ...authenticated, (_req, res) => {
  res.status(405).json({
    jsonrpc: '2.0',
    error: { code: -32000, message: 'Method not allowed in stateless mode.' },
    id: null,
  });
});
app.delete('/mcp', ...authenticated, (_req, res) => {
  res.status(405).json({
    jsonrpc: '2.0',
    error: { code: -32000, message: 'Method not allowed in stateless mode.' },
    id: null,
  });
});

app.use('/rest', ...authenticated, buildRestRouter(config));

app.use((_req, res) => {
  res.status(404).json({ error: 'not_found' });
});

const server = app.listen(config.port, () => {
  process.stdout.write(
    `${JSON.stringify({
      at: new Date().toISOString(),
      level: 'info',
      msg: 'zitch-meta-connector listening',
      port: config.port,
      readOnly: config.readOnly,
      instanceId: randomUUID(),
    })}\n`,
  );
});

function shutdown(signal: string): void {
  process.stdout.write(`${JSON.stringify({ level: 'info', msg: `received ${signal}, shutting down` })}\n`);
  server.close(() => process.exit(0));
  // Belt-and-braces: force-exit if connections don't drain in time.
  setTimeout(() => process.exit(1), 10_000).unref();
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));

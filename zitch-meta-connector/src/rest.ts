/**
 * Plain REST mirror of every MCP tool, at `GET /rest/<tool-name>`.
 *
 * Not required for Claude (which speaks MCP directly against `/mcp`) — this
 * exists so the same read-only logic can be wired up as a ChatGPT GPT Action
 * later, which needs a plain OpenAPI-described REST surface rather than the
 * MCP wire protocol. See openapi.yaml for the matching spec. Every handler
 * here shares the exact same auth, rate-limiting, audit logging, input
 * validation, and tool implementation as the MCP path — there is exactly one
 * implementation of each tool (src/tools/*.ts); this file only adapts it to
 * HTTP GET query params instead of an MCP tool call.
 */
import type { Request, Response, Router } from 'express';
import { Router as createRouter } from 'express';
import { ZodError } from 'zod';

import type { Config } from './config.js';
import { withAudit } from './audit.js';
import { MetaApiError } from './metaClient.js';
import { redactDeep } from './redact.js';
import { TOOL_REGISTRY } from './tools/registry.js';

/** Query params arrive as strings; coerce the couple of numeric fields the
 * schemas expect before validation, rather than loosening the schemas
 * themselves (which also validate the MCP path, where args are already
 * typed). */
function coerceQuery(query: Request['query']): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(query)) {
    if (typeof value !== 'string') continue; // reject arrays/nested objects outright
    if (key === 'limit' || key === 'lookbackHours') {
      const n = Number(value);
      out[key] = Number.isFinite(n) ? n : value;
    } else {
      out[key] = value;
    }
  }
  return out;
}

export function buildRestRouter(config: Config): Router {
  const router = createRouter();

  for (const tool of TOOL_REGISTRY) {
    const path = `/${tool.name.replace(/_/g, '-')}`;
    router.get(path, async (req: Request, res: Response) => {
      const meta = {
        tool: tool.name,
        keyFingerprint: (res.locals.keyFingerprint as string | undefined) ?? '(none)',
        authMethod: res.locals.authMethod as string | undefined,
        ip: req.ip,
      };
      try {
        const result = await withAudit(meta, async () => {
          const input = tool.schema.parse(coerceQuery(req.query));
          return tool.handler(config, input);
        });
        res.status(200).json(redactDeep(result));
      } catch (err) {
        sendRestError(res, err);
      }
    });
  }

  router.get('/', (_req, res) => {
    res.status(200).json({
      tools: TOOL_REGISTRY.map((t) => ({
        name: t.name,
        path: `/rest/${t.name.replace(/_/g, '-')}`,
        description: t.description,
      })),
    });
  });

  return router;
}

function sendRestError(res: Response, err: unknown): void {
  if (err instanceof ZodError) {
    res.status(400).json({ error: 'invalid_input', message: err.message, issues: err.issues });
    return;
  }
  if (err instanceof MetaApiError) {
    res.status(err.status && err.status >= 400 && err.status < 500 ? err.status : 502).json({
      error: 'meta_api_error',
      message: err.message,
    });
    return;
  }
  res.status(500).json({ error: 'internal_error', message: 'Internal error handling this request.' });
}

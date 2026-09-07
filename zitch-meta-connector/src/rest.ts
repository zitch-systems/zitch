/**
 * Plain REST mirror of every MCP tool: reads at `GET /rest/<tool-name>`,
 * writes at `POST /rest/<tool-name>`.
 *
 * Not required for Claude (which speaks MCP directly against `/mcp`) — this
 * exists so the same logic can be wired up as a ChatGPT GPT Action
 * later, which needs a plain OpenAPI-described REST surface rather than the
 * MCP wire protocol. See openapi.yaml for the matching spec. Every handler
 * here shares the exact same auth, rate-limiting, audit logging, input
 * validation, and tool implementation as the MCP path — there is exactly one
 * implementation of each tool (src/tools/*.ts); this file only adapts it to
 * HTTP query params / JSON bodies instead of an MCP tool call.
 */
import type { Request, Response, Router } from 'express';
import { Router as createRouter } from 'express';
import { ZodError } from 'zod';

import type { Config } from './config.js';
import { withAudit } from './audit.js';
import { MetaApiError } from './metaClient.js';
import { redactDeep } from './redact.js';
import { targetOf, toolsFor } from './tools/registry.js';
import { ConfirmationError } from './tools/writes.js';

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

  for (const tool of toolsFor(config)) {
    const path = `/${tool.name.replace(/_/g, '-')}`;

    const handle = async (req: Request, res: Response): Promise<void> => {
      const raw = tool.write ? ((req.body ?? {}) as Record<string, unknown>) : coerceQuery(req.query);
      const meta = {
        tool: tool.name,
        keyFingerprint: (res.locals.keyFingerprint as string | undefined) ?? '(none)',
        authMethod: res.locals.authMethod as string | undefined,
        ip: req.ip,
        ...(tool.write ? { write: true, target: targetOf(raw) } : {}),
      };
      try {
        const result = await withAudit(meta, async () => {
          const input = tool.schema.parse(raw);
          return tool.handler(config, input);
        });
        res.status(200).json(redactDeep(result));
      } catch (err) {
        sendRestError(res, err);
      }
    };

    if (tool.write) {
      // POST, never GET. A GET that mutates is a real hazard rather than a
      // style point: browser prefetch, link previews, and automatic retries
      // all re-issue GETs freely, any of which would silently re-run the
      // write. GET on a write path is answered with 405.
      router.post(path, handle);
      router.get(path, (_req, res) => {
        res.status(405).json({
          error: 'method_not_allowed',
          message: `${tool.name} modifies Meta configuration and must be called with POST.`,
        });
      });
    } else {
      router.get(path, handle);
    }
  }

  router.get('/', (_req, res) => {
    res.status(200).json({
      readOnly: config.readOnly,
      tools: toolsFor(config).map((t) => ({
        name: t.name,
        method: t.write ? 'POST' : 'GET',
        path: `/rest/${t.name.replace(/_/g, '-')}`,
        write: Boolean(t.write),
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
  // The confirmation interlock exists to be corrected, so its message has to
  // reach the caller — collapsing it into a generic 500 would leave them
  // guessing at exactly the moment the guardrail is trying to help. Safe to
  // surface: it contains Meta object identifiers only, never a secret.
  if (err instanceof ConfirmationError) {
    res.status(400).json({ error: 'confirmation_required', message: err.message });
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

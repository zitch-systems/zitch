/**
 * Builds one McpServer instance per HTTP request (stateless mode — no
 * sessionIdGenerator), registering all six read-only tools from the shared
 * registry. Stateless is deliberate: every request is independently
 * authenticated (see auth.ts) and every tool call is a fresh, idempotent
 * Graph API read, so there is no session state worth keeping between
 * requests, and no session store to secure or leak.
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { ZodError } from 'zod';

import type { Config } from './config.js';
import { withAudit } from './audit.js';
import { MetaApiError } from './metaClient.js';
import { redactDeep } from './redact.js';
import { TOOL_REGISTRY } from './tools/registry.js';

export interface RequestMeta {
  keyFingerprint: string;
  ip: string | undefined;
}

export function buildMcpServer(config: Config, requestMeta: RequestMeta): McpServer {
  const server = new McpServer(
    { name: 'zitch-meta-connector', version: '1.0.0' },
    {
      capabilities: { tools: {} },
      instructions:
        'Read-only maintenance/configuration tools for the Zitch WhatsApp Cloud API and Meta ' +
        'Business API. No customer data (PINs, biometrics, balances, transactions, personal ' +
        "data) is ever reachable through this server — it only talks to Meta's Graph API. " +
        'No write, delete, token-rotation, or payment tools are exposed.',
    },
  );

  for (const tool of TOOL_REGISTRY) {
    server.registerTool(
      tool.name,
      {
        title: tool.title,
        description: tool.description,
        inputSchema: tool.schema.shape,
      },
      async (rawArgs: unknown) => {
        return withAudit({ tool: tool.name, ...requestMeta }, async () => {
          try {
            const input = tool.schema.parse(rawArgs ?? {});
            const result = await tool.handler(config, input);
            return {
              content: [{ type: 'text' as const, text: JSON.stringify(redactDeep(result), null, 2) }],
            };
          } catch (err) {
            return {
              isError: true,
              content: [{ type: 'text' as const, text: safeErrorMessage(err) }],
            };
          }
        });
      },
    );
  }

  return server;
}

/** Never lets a raw exception (which could include stack traces, internal
 * paths, or — in a worst case someone missed — a header value) reach a
 * client. MetaApiError messages are already safe (Meta's own error text);
 * everything else collapses to a generic message. */
function safeErrorMessage(err: unknown): string {
  if (err instanceof MetaApiError) {
    return `Meta API error: ${err.message}`;
  }
  if (err instanceof ZodError) {
    // A zod validation error — its messages are all about the caller's input
    // shape, so safe to return as-is.
    return `Invalid input: ${err.message}`;
  }
  return 'Internal error handling this tool call.';
}

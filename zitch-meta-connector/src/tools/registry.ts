/**
 * Single source of truth for the six tools this connector exposes — shared by
 * the MCP server registration (src/server.ts) and the plain REST mirror
 * (src/rest.ts) so both surfaces can never drift apart on name, schema, or
 * behavior. Every tool here is read-only: none of them can create, modify,
 * delete, or rotate anything at Meta, and none of them touch a Zitch
 * customer's PIN, biometric enrollment, balance, or transaction history —
 * this connector has no code path that could even reach that data, since it
 * only ever talks to the Meta Graph API, never the Zitch backend or database.
 */
import type { z } from 'zod';

import type { Config } from '../config.js';
import {
  checkPhoneNumberConfigSchema,
  checkWebhookStatusSchema,
  inspectFailedDeliveriesSchema,
  inspectWebhookEventsSchema,
  listMessageTemplatesSchema,
  verifyMetaCredentialsSchema,
} from '../schemas.js';
import { checkWebhookStatus } from './webhookStatus.js';
import { checkPhoneNumberConfig } from './phoneNumberConfig.js';
import { listMessageTemplates } from './messageTemplates.js';
import { inspectFailedDeliveries } from './failedDeliveries.js';
import { inspectWebhookEvents } from './webhookEvents.js';
import { verifyMetaCredentials } from './verifyCredentials.js';

export interface ToolDefinition {
  /** snake_case tool name, as it appears to an MCP client. */
  name: string;
  title: string;
  description: string;
  /** The tool's own zod object schema — each entry's `handler` is written
   * against this exact schema's inferred type at its definition site (see
   * webhookStatus.ts et al.). The registry array below intentionally widens
   * `handler`'s parameter to `any` so tools with different input shapes can
   * share one array type; every real call site still goes through
   * `schema.parse()` first (src/server.ts, src/rest.ts), so the narrowing
   * that matters — rejecting a malformed request — happens at runtime
   * regardless of this array's static type. */
  schema: z.ZodObject<any>;
  handler: (config: Config, input: any) => Promise<unknown>;
}

export const TOOL_REGISTRY: readonly ToolDefinition[] = [
  {
    name: 'check_whatsapp_webhook_status',
    title: 'Check WhatsApp webhook status',
    description:
      "Reports which app(s) are subscribed to this WhatsApp Business Account's webhook events " +
      'and which event fields each is subscribed to. An empty subscriber list is the most common ' +
      'cause of "webhook events stopped arriving".',
    schema: checkWebhookStatusSchema,
    handler: checkWebhookStatus,
  },
  {
    name: 'check_whatsapp_phone_number_config',
    title: 'Check WhatsApp phone number configuration',
    description:
      "Reads Zitch's own outbound WhatsApp number configuration: verification status, quality " +
      'rating, messaging limit tier, and display-name review status.',
    schema: checkPhoneNumberConfigSchema,
    handler: checkPhoneNumberConfig,
  },
  {
    name: 'list_whatsapp_message_templates',
    title: 'List WhatsApp message templates',
    description:
      'Lists the WABA\'s message templates with their approval status, category, language, and ' +
      'quality score. Template content is business-authored boilerplate, not customer data.',
    schema: listMessageTemplatesSchema,
    handler: listMessageTemplates,
  },
  {
    name: 'inspect_failed_message_deliveries',
    title: 'Inspect failed message deliveries',
    description:
      'Returns aggregate daily sent/delivered counts for a bounded recent window (Meta does not ' +
      'expose a per-message failure log via the Graph API — see the tool result\'s `note` field ' +
      'for the exact limitation).',
    schema: inspectFailedDeliveriesSchema,
    handler: inspectFailedDeliveries,
  },
  {
    name: 'inspect_webhook_events',
    title: 'Inspect webhook event configuration',
    description:
      'Lists which webhook event types (fields) are currently configured per subscribed app. ' +
      'This is current configuration, not a historical delivery log — see the tool result\'s ' +
      '`note` field for the exact limitation.',
    schema: inspectWebhookEventsSchema,
    handler: inspectWebhookEvents,
  },
  {
    name: 'verify_meta_credentials',
    title: 'Verify Meta API credentials',
    description:
      'Confirms META_ACCESS_TOKEN is valid and can read the configured WABA and phone number, ' +
      'by making a minimal read against each. Never returns the token itself.',
    schema: verifyMetaCredentialsSchema,
    handler: (config: Config) => verifyMetaCredentials(config),
  },
];

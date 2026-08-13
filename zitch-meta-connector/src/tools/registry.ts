/**
 * Single source of truth for every tool this connector exposes — shared by
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
  businessProfileSchema,
  checkPhoneNumberConfigSchema,
  checkWebhookStatusSchema,
  conversationAnalyticsSchema,
  inspectFailedDeliveriesSchema,
  inspectFlowSchema,
  inspectWebhookEventsSchema,
  listFlowsSchema,
  listMessageTemplatesSchema,
  listPhoneNumbersSchema,
  publishedFlowScreensSchema,
  verifyMetaCredentialsSchema,
  wabaDetailsSchema,
} from '../schemas.js';
import { checkWebhookStatus } from './webhookStatus.js';
import { checkPhoneNumberConfig } from './phoneNumberConfig.js';
import { listMessageTemplates } from './messageTemplates.js';
import { inspectFailedDeliveries } from './failedDeliveries.js';
import { inspectWebhookEvents } from './webhookEvents.js';
import { verifyMetaCredentials } from './verifyCredentials.js';
import { getPublishedFlowScreens, inspectFlow, listFlows } from './flows.js';
import {
  getBusinessProfile,
  getConversationAnalytics,
  getWabaDetails,
  listPhoneNumbers,
} from './account.js';

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
  {
    name: 'list_whatsapp_flows',
    title: 'List WhatsApp Flows',
    description:
      "Lists the WABA's Flows (the interactive multi-screen forms — Zitch's PIN, signup and " +
      'identity ladders) with their publish status and categories. Distinct from message ' +
      'templates, which are the pre-approved message bodies.',
    schema: listFlowsSchema,
    handler: listFlows,
  },
  {
    name: 'inspect_whatsapp_flow',
    title: 'Inspect a WhatsApp Flow',
    description:
      "One Flow's status, categories, and — most usefully — its validation_errors, which are " +
      'exactly what Meta refuses a publish on. Also returns the existing preview URL without ' +
      'regenerating it.',
    schema: inspectFlowSchema,
    handler: inspectFlow,
  },
  {
    name: 'get_published_flow_screens',
    title: 'Get a published Flow\'s screens and routing',
    description:
      "The screen inventory and routing model of the Flow JSON Meta actually has published — " +
      "including which screens are routing roots (the only ones a Flow may OPEN on; violating " +
      'that is the 131009 "screen not allowed as first screen" rejection), plus any dangling ' +
      'routes or unreachable screens. Compare the screen list against the repo\'s ' +
      'pin_flow.json to catch a Flow that is one publish behind the code.',
    schema: publishedFlowScreensSchema,
    handler: getPublishedFlowScreens,
  },
  {
    name: 'get_waba_details',
    title: 'Get WhatsApp Business Account details',
    description:
      "The WABA's own name, timezone, template namespace, and — the ones that silently gate " +
      'Flows, template approval and messaging limits — its account review and business ' +
      'verification status.',
    schema: wabaDetailsSchema,
    handler: getWabaDetails,
  },
  {
    name: 'list_whatsapp_phone_numbers',
    title: 'List WhatsApp phone numbers',
    description:
      'Every number on the WABA with its quality rating, verification status, messaging limit ' +
      'tier and display-name state — the wider view that check_whatsapp_phone_number_config ' +
      'gives for one number.',
    schema: listPhoneNumbersSchema,
    handler: listPhoneNumbers,
  },
  {
    name: 'get_whatsapp_business_profile',
    title: 'Get the public WhatsApp business profile',
    description:
      "The profile customers see on the business: about text, description, address, email, " +
      'websites and vertical. Business-authored content, not customer data.',
    schema: businessProfileSchema,
    handler: getBusinessProfile,
  },
  {
    name: 'get_conversation_analytics',
    title: 'Get conversation volume and cost',
    description:
      'Aggregate conversation counts and billing over a bounded window, grouped by category ' +
      '(marketing / utility / authentication / service). Aggregate totals only — no ' +
      'recipients, no message content, no per-customer detail.',
    schema: conversationAnalyticsSchema,
    handler: getConversationAnalytics,
  },
];

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
  createFlowSchema,
  createMessageTemplateSchema,
  deleteMessageTemplateSchema,
  deprecateFlowSchema,
  inspectFailedDeliveriesSchema,
  inspectFlowSchema,
  inspectWebhookEventsSchema,
  listFlowsSchema,
  listMessageTemplatesSchema,
  listPhoneNumbersSchema,
  publishFlowSchema,
  publishedFlowScreensSchema,
  updateBusinessProfileSchema,
  updateFlowJsonSchema,
  updateMessageTemplateSchema,
  verifyMetaCredentialsSchema,
  wabaDetailsSchema,
} from '../schemas.js';
import {
  createFlow,
  createMessageTemplate,
  deleteMessageTemplate,
  deprecateFlow,
  publishFlow,
  updateBusinessProfile,
  updateFlowJson,
  updateMessageTemplate,
} from './writes.js';
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
  /** True for tools that MUTATE Meta configuration. These are filtered out
   * entirely when the connector is in read-only mode — see toolsFor(). */
  write?: boolean;
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

  // ------------------------------------------------------------- WRITE tools
  // Registered ONLY when META_ALLOW_WRITES is on (see toolsFor). Meta
  // configuration only: no message sending, no webhook subscription changes,
  // no phone-number registration — see the header comment in writes.ts for
  // where that line sits and why.
  {
    name: 'create_message_template',
    title: 'Create a message template',
    description:
      'Submits a new message template to Meta for review. Requires `confirm` to equal the ' +
      'template name.',
    schema: createMessageTemplateSchema,
    handler: createMessageTemplate,
    write: true,
  },
  {
    name: 'update_message_template',
    title: 'Edit a message template',
    description:
      'Edits an existing template\'s components or category. Editing an approved template sends ' +
      'it back through Meta review. Requires `confirm` to equal the template ID.',
    schema: updateMessageTemplateSchema,
    handler: updateMessageTemplate,
    write: true,
  },
  {
    name: 'delete_message_template',
    title: 'Delete a message template',
    description:
      'Deletes a template by name, which removes EVERY language version of it. Any send naming ' +
      'it will fail afterwards. Requires `confirm` to equal the template name.',
    schema: deleteMessageTemplateSchema,
    handler: deleteMessageTemplate,
    write: true,
  },
  {
    name: 'update_whatsapp_business_profile',
    title: 'Update the public business profile',
    description:
      'Updates the profile customers see: about, address, description, email, vertical, ' +
      'websites. Requires `confirm` to equal the phone number ID.',
    schema: updateBusinessProfileSchema,
    handler: updateBusinessProfile,
    write: true,
  },
  {
    name: 'create_whatsapp_flow',
    title: 'Create a WhatsApp Flow',
    description:
      'Creates a new, empty Flow in draft. Upload its JSON with update_whatsapp_flow_json, then ' +
      'publish. Requires `confirm` to equal the Flow name.',
    schema: createFlowSchema,
    handler: createFlow,
    write: true,
  },
  {
    name: 'update_whatsapp_flow_json',
    title: "Replace a Flow's draft JSON",
    description:
      "Uploads a Flow JSON document, replacing the draft. Does NOT go live until published, and " +
      'returns any validation_errors that would block a publish. This is the tool that removes ' +
      'the copy-paste-into-WhatsApp-Manager step. Requires `confirm` to equal the Flow ID.',
    schema: updateFlowJsonSchema,
    handler: updateFlowJson,
    write: true,
  },
  {
    name: 'publish_whatsapp_flow',
    title: 'Publish a WhatsApp Flow',
    description:
      'Makes the Flow live for customers. A published Flow can no longer be edited in place. ' +
      'Requires `confirm` to equal the Flow ID.',
    schema: publishFlowSchema,
    handler: publishFlow,
    write: true,
  },
  {
    name: 'deprecate_whatsapp_flow',
    title: 'Deprecate a WhatsApp Flow',
    description:
      'IRREVERSIBLE. No new session can open on this Flow afterwards. If it is the Flow the PIN ' +
      'or signup ladder uses, that journey breaks until another is published. Requires ' +
      '`confirm` to equal the Flow ID.',
    schema: deprecateFlowSchema,
    handler: deprecateFlow,
    write: true,
  },
];

/**
 * The tools this connector actually exposes, given its mode.
 *
 * In read-only mode write tools are ABSENT rather than present-and-refusing:
 * they do not appear in tools/list, the REST mirror, or the MCP dispatch
 * table. A tool that cannot be named cannot be called by mistake, and a model
 * reading the tool list is never tempted by a capability it does not have.
 */
export function toolsFor(config: Config): readonly ToolDefinition[] {
  return config.readOnly ? TOOL_REGISTRY.filter((t) => !t.write) : TOOL_REGISTRY;
}

/**
 * The resource a write call is about, for the audit log — read from whichever
 * identifying field the tool uses.
 *
 * Deliberately reads the REQUEST, not the result: a call that fails still has
 * to leave a record of what it was aiming at, and that is exactly the entry an
 * incident review wants. Only ever returns Meta object identifiers, never
 * free-form content.
 */
export function targetOf(input: Record<string, unknown>): string | undefined {
  for (const key of ['flowId', 'templateId', 'name', 'phoneNumberId', 'wabaId']) {
    const value = input[key];
    if (typeof value === 'string' && value) return `${key}=${value.slice(0, 120)}`;
  }
  return undefined;
}

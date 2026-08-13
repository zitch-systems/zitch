/**
 * Input validation for every tool. Nothing reaches metaClient.ts without
 * passing through one of these first — a maintenance/config connector talking
 * to Meta's Graph API is a small, well-known surface, and every parameter has
 * a real, checkable shape (an ID is digits, a window is bounded, a cursor is
 * an opaque bounded-length string). Rejecting anything else outright removes
 * a whole class of "what if someone passes a huge/weird string" concerns
 * before it ever reaches an outbound HTTP call.
 */
import { z } from 'zod';

/** Meta object IDs (WABA ID, phone number ID, message template ID) are decimal digit strings. */
const metaId = z
  .string()
  .trim()
  .regex(/^\d{1,32}$/, 'must be a numeric Meta object ID');

const paginationCursor = z
  .string()
  .trim()
  .min(1)
  .max(2048)
  .optional()
  .describe('Opaque `after` cursor from a previous response\'s paging.cursors.after, for the next page.');

const pageLimit = z
  .number()
  .int()
  .min(1)
  .max(100)
  .optional()
  .describe('Max items to return (1-100). Defaults to a safe page size.');

export const checkWebhookStatusSchema = z
  .object({
    wabaId: metaId
      .optional()
      .describe('WhatsApp Business Account ID to check. Defaults to META_WABA_ID.'),
  })
  .strict();

export const checkPhoneNumberConfigSchema = z
  .object({
    phoneNumberId: metaId
      .optional()
      .describe('Phone number ID to inspect. Defaults to META_PHONE_NUMBER_ID.'),
  })
  .strict();

export const listMessageTemplatesSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
    limit: pageLimit,
    after: paginationCursor,
    nameFilter: z
      .string()
      .trim()
      .min(1)
      .max(512)
      .optional()
      .describe('Case-sensitive exact template name to filter on.'),
  })
  .strict();

/** Bounds a lookback window to something a maintenance tool actually needs —
 * never an unbounded "since the beginning of time" query against Meta. */
const lookbackHours = z
  .number()
  .int()
  .min(1)
  .max(24 * 30, 'lookback cannot exceed 30 days (720 hours)')
  .optional()
  .describe('How far back to look, in hours (1-720). Defaults to 24.');

export const inspectFailedDeliveriesSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
    lookbackHours,
  })
  .strict();

export const inspectWebhookEventsSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
  })
  .strict();

export const verifyMetaCredentialsSchema = z.object({}).strict();

// --- Flows -----------------------------------------------------------------
export const listFlowsSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
    limit: pageLimit,
  })
  .strict();

export const inspectFlowSchema = z
  .object({
    flowId: metaId.describe('Flow ID, from list_whatsapp_flows.'),
  })
  .strict();

export const publishedFlowScreensSchema = z
  .object({
    flowId: metaId.describe('Flow ID, from list_whatsapp_flows.'),
  })
  .strict();

// --- Account / number configuration ---------------------------------------
export const wabaDetailsSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
  })
  .strict();

export const listPhoneNumbersSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
  })
  .strict();

export const businessProfileSchema = z
  .object({
    phoneNumberId: metaId
      .optional()
      .describe('Phone number ID whose public profile to read. Defaults to META_PHONE_NUMBER_ID.'),
  })
  .strict();

export const conversationAnalyticsSchema = z
  .object({
    wabaId: metaId.optional().describe('WhatsApp Business Account ID. Defaults to META_WABA_ID.'),
    lookbackHours,
  })
  .strict();

// --- WRITE tools -----------------------------------------------------------
// Registered only when META_ALLOW_WRITES is on. Every one carries `confirm`,
// which must restate the exact resource being changed — see writes.ts for why
// that interlock exists.
const confirm = z
  .string()
  .min(1)
  .max(200)
  .describe(
    'Safety interlock: must exactly equal the name or ID of the resource this call will ' +
      'change. The call is refused otherwise.',
  );

const templateName = z
  .string()
  .trim()
  .min(1)
  .max(512)
  .regex(/^[a-z0-9_]+$/, 'Meta template names are lowercase letters, digits and underscores only');

/** Template components are Meta's own nested structure (HEADER/BODY/FOOTER/
 * BUTTONS). Passed through rather than re-modelled: mirroring Meta's schema
 * here would go stale every time they add a component type, and the API is the
 * authority on what is valid. Bounded so it cannot be used as a payload bomb. */
const templateComponents = z
  .array(z.record(z.string(), z.unknown()))
  .min(1)
  .max(20)
  .describe('Meta message-template components array (HEADER / BODY / FOOTER / BUTTONS).');

export const createMessageTemplateSchema = z
  .object({
    wabaId: metaId.optional(),
    name: templateName.describe('Template name. Lowercase, digits and underscores.'),
    language: z.string().trim().min(2).max(10).describe('Language code, e.g. en_US.'),
    category: z
      .enum(['AUTHENTICATION', 'MARKETING', 'UTILITY'])
      .describe('Meta template category.'),
    components: templateComponents,
    confirm: confirm.describe('Must equal the template `name`.'),
  })
  .strict();

export const updateMessageTemplateSchema = z
  .object({
    templateId: metaId.describe('Template ID to edit.'),
    components: templateComponents.optional(),
    category: z.enum(['AUTHENTICATION', 'MARKETING', 'UTILITY']).optional(),
    confirm: confirm.describe('Must equal `templateId`.'),
  })
  .strict();

export const deleteMessageTemplateSchema = z
  .object({
    wabaId: metaId.optional(),
    name: templateName.describe('Template name to delete (removes ALL language versions).'),
    hsmId: metaId.optional().describe('Optional specific template ID, to delete one version only.'),
    confirm: confirm.describe('Must equal the template `name`.'),
  })
  .strict();

export const updateBusinessProfileSchema = z
  .object({
    phoneNumberId: metaId.optional(),
    about: z.string().max(139).optional(),
    address: z.string().max(256).optional(),
    description: z.string().max(512).optional(),
    email: z.string().email().max(128).optional(),
    vertical: z.string().max(64).optional(),
    websites: z.array(z.string().url()).max(2).optional(),
    confirm: confirm.describe('Must equal the phone number ID being updated.'),
  })
  .strict();

export const createFlowSchema = z
  .object({
    wabaId: metaId.optional(),
    name: z.string().trim().min(1).max(200).describe('Flow name.'),
    categories: z
      .array(
        z.enum([
          'SIGN_UP',
          'SIGN_IN',
          'APPOINTMENT_BOOKING',
          'LEAD_GENERATION',
          'CONTACT_US',
          'CUSTOMER_SUPPORT',
          'SURVEY',
          'OTHER',
        ]),
      )
      .min(1)
      .max(8),
    confirm: confirm.describe('Must equal the Flow `name`.'),
  })
  .strict();

export const updateFlowJsonSchema = z
  .object({
    flowId: metaId.describe('Flow ID whose draft JSON to replace.'),
    flowJson: z
      .string()
      .min(2)
      // Generous, because the real pin_flow.json is ~246KB with the logo
      // inlined as base64 — but still bounded so this cannot be a memory bomb.
      .max(4_000_000)
      .describe('The complete Flow JSON document, as a string.'),
    confirm: confirm.describe('Must equal `flowId`.'),
  })
  .strict();

export const publishFlowSchema = z
  .object({
    flowId: metaId.describe('Flow ID to publish. This makes it live for customers.'),
    confirm: confirm.describe('Must equal `flowId`.'),
  })
  .strict();

export const deprecateFlowSchema = z
  .object({
    flowId: metaId.describe('Flow ID to deprecate. IRREVERSIBLE.'),
    confirm: confirm.describe('Must equal `flowId`.'),
  })
  .strict();

export type CreateMessageTemplateInput = z.infer<typeof createMessageTemplateSchema>;
export type UpdateMessageTemplateInput = z.infer<typeof updateMessageTemplateSchema>;
export type DeleteMessageTemplateInput = z.infer<typeof deleteMessageTemplateSchema>;
export type UpdateBusinessProfileInput = z.infer<typeof updateBusinessProfileSchema>;
export type CreateFlowInput = z.infer<typeof createFlowSchema>;
export type UpdateFlowJsonInput = z.infer<typeof updateFlowJsonSchema>;
export type PublishFlowInput = z.infer<typeof publishFlowSchema>;
export type DeprecateFlowInput = z.infer<typeof deprecateFlowSchema>;

export type ListFlowsInput = z.infer<typeof listFlowsSchema>;
export type InspectFlowInput = z.infer<typeof inspectFlowSchema>;
export type PublishedFlowScreensInput = z.infer<typeof publishedFlowScreensSchema>;
export type WabaDetailsInput = z.infer<typeof wabaDetailsSchema>;
export type ListPhoneNumbersInput = z.infer<typeof listPhoneNumbersSchema>;
export type BusinessProfileInput = z.infer<typeof businessProfileSchema>;
export type ConversationAnalyticsInput = z.infer<typeof conversationAnalyticsSchema>;

export type CheckWebhookStatusInput = z.infer<typeof checkWebhookStatusSchema>;
export type CheckPhoneNumberConfigInput = z.infer<typeof checkPhoneNumberConfigSchema>;
export type ListMessageTemplatesInput = z.infer<typeof listMessageTemplatesSchema>;
export type InspectFailedDeliveriesInput = z.infer<typeof inspectFailedDeliveriesSchema>;
export type InspectWebhookEventsInput = z.infer<typeof inspectWebhookEventsSchema>;
export type VerifyMetaCredentialsInput = z.infer<typeof verifyMetaCredentialsSchema>;

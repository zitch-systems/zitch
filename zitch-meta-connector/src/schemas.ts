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

export type CheckWebhookStatusInput = z.infer<typeof checkWebhookStatusSchema>;
export type CheckPhoneNumberConfigInput = z.infer<typeof checkPhoneNumberConfigSchema>;
export type ListMessageTemplatesInput = z.infer<typeof listMessageTemplatesSchema>;
export type InspectFailedDeliveriesInput = z.infer<typeof inspectFailedDeliveriesSchema>;
export type InspectWebhookEventsInput = z.infer<typeof inspectWebhookEventsSchema>;
export type VerifyMetaCredentialsInput = z.infer<typeof verifyMetaCredentialsSchema>;

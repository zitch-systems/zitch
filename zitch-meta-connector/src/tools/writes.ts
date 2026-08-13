/**
 * The write tools — Meta CONFIGURATION only.
 *
 * Scope, and the reasoning behind where the line sits:
 *
 *   IN  message templates (create / edit / delete), the public business
 *       profile, and Flows (create / update JSON / publish / deprecate).
 *   OUT sending messages. This connector deliberately has no path to
 *       `whatsapp_business_messaging`, so even a total compromise of it
 *       cannot send a WhatsApp message from Zitch's verified bank number to
 *       a customer. That is the capability that turns a breach into
 *       customer-facing fraud, and it buys nothing for maintenance work.
 *   OUT webhook subscribe / unsubscribe. The same WABA carries the live
 *       customer banking channel; unsubscribing it stops every inbound
 *       message being processed, with no error anywhere — a silent outage.
 *       Changing that stays a deliberate act in WhatsApp Manager.
 *   OUT phone number registration, token rotation, payments.
 *
 * Every tool here is registered ONLY when config.readOnly is false, and every
 * one takes a `confirm` argument that must restate the exact resource being
 * changed. The interlock is not about a human typo — it is about an AI acting
 * on a misread instruction. "Tidy up the old flows" should not be one
 * inference away from deprecating the Flow that PIN entry depends on.
 */
import type { Config } from '../config.js';
import { graphDelete, graphPost } from '../metaClient.js';
import type {
  CreateFlowInput,
  CreateMessageTemplateInput,
  DeleteMessageTemplateInput,
  DeprecateFlowInput,
  PublishFlowInput,
  UpdateBusinessProfileInput,
  UpdateFlowJsonInput,
  UpdateMessageTemplateInput,
} from '../schemas.js';

export class ConfirmationError extends Error {
  constructor(expected: string, got: string) {
    super(
      `Confirmation mismatch: this call would change "${expected}", but confirm was ` +
        `"${got}". Re-issue with confirm set to exactly "${expected}" if that is genuinely ` +
        'the resource you mean to change.',
    );
    this.name = 'ConfirmationError';
  }
}

/** The interlock. Throws unless the caller restated the exact target. */
function assertConfirmed(config: Config, expected: string, got: string): void {
  if (!config.requireWriteConfirmation) return;
  if (got !== expected) throw new ConfirmationError(expected, got);
}

// ------------------------------------------------------- message templates
export interface CreateTemplateResult {
  action: 'create_message_template';
  wabaId: string;
  name: string;
  id: string | undefined;
  status: string | undefined;
  category: string | undefined;
}

export async function createMessageTemplate(
  config: Config,
  input: CreateMessageTemplateInput,
): Promise<CreateTemplateResult> {
  assertConfirmed(config, input.name, input.confirm);
  const wabaId = input.wabaId ?? config.metaWabaId;
  const res = (await graphPost(config, `${wabaId}/message_templates`, {
    name: input.name,
    language: input.language,
    category: input.category,
    components: input.components,
  })) as { id?: string; status?: string; category?: string };
  return {
    action: 'create_message_template',
    wabaId,
    name: input.name,
    id: res.id,
    status: res.status,
    category: res.category,
  };
}

export interface UpdateTemplateResult {
  action: 'update_message_template';
  templateId: string;
  success: boolean;
  note: string;
}

export async function updateMessageTemplate(
  config: Config,
  input: UpdateMessageTemplateInput,
): Promise<UpdateTemplateResult> {
  assertConfirmed(config, input.templateId, input.confirm);
  const body: Record<string, unknown> = {};
  if (input.components) body.components = input.components;
  if (input.category) body.category = input.category;
  const res = (await graphPost(config, input.templateId, body)) as { success?: boolean };
  return {
    action: 'update_message_template',
    templateId: input.templateId,
    success: res.success !== false,
    note:
      'Editing an approved template sends it back through Meta review, and a template can ' +
      'only be edited a limited number of times per period.',
  };
}

export interface DeleteTemplateResult {
  action: 'delete_message_template';
  wabaId: string;
  name: string;
  success: boolean;
  note: string;
}

export async function deleteMessageTemplate(
  config: Config,
  input: DeleteMessageTemplateInput,
): Promise<DeleteTemplateResult> {
  assertConfirmed(config, input.name, input.confirm);
  const wabaId = input.wabaId ?? config.metaWabaId;
  const res = (await graphDelete(config, `${wabaId}/message_templates`, {
    name: input.name,
    ...(input.hsmId ? { hsm_id: input.hsmId } : {}),
  })) as { success?: boolean };
  return {
    action: 'delete_message_template',
    wabaId,
    name: input.name,
    success: res.success !== false,
    note:
      'Deleting by name removes EVERY language version of that template. Any message send ' +
      'naming it will fail from now on.',
  };
}

// -------------------------------------------------------- business profile
export interface UpdateProfileResult {
  action: 'update_whatsapp_business_profile';
  phoneNumberId: string;
  updatedFields: string[];
  success: boolean;
}

export async function updateBusinessProfile(
  config: Config,
  input: UpdateBusinessProfileInput,
): Promise<UpdateProfileResult> {
  const phoneNumberId = input.phoneNumberId ?? config.metaPhoneNumberId;
  assertConfirmed(config, phoneNumberId, input.confirm);

  const body: Record<string, unknown> = { messaging_product: 'whatsapp' };
  const fields: string[] = [];
  for (const key of ['about', 'address', 'description', 'email', 'vertical'] as const) {
    const value = input[key];
    if (value !== undefined) {
      body[key] = value;
      fields.push(key);
    }
  }
  if (input.websites) {
    body.websites = input.websites;
    fields.push('websites');
  }
  if (fields.length === 0) {
    throw new Error('Nothing to update: supply at least one profile field.');
  }

  const res = (await graphPost(config, `${phoneNumberId}/whatsapp_business_profile`, body)) as {
    success?: boolean;
  };
  return {
    action: 'update_whatsapp_business_profile',
    phoneNumberId,
    updatedFields: fields,
    success: res.success !== false,
  };
}

// ------------------------------------------------------------------ Flows
export interface CreateFlowResult {
  action: 'create_whatsapp_flow';
  wabaId: string;
  name: string;
  id: string | undefined;
}

export async function createFlow(config: Config, input: CreateFlowInput): Promise<CreateFlowResult> {
  assertConfirmed(config, input.name, input.confirm);
  const wabaId = input.wabaId ?? config.metaWabaId;
  const res = (await graphPost(config, `${wabaId}/flows`, {
    name: input.name,
    categories: input.categories,
  })) as { id?: string };
  return { action: 'create_whatsapp_flow', wabaId, name: input.name, id: res.id };
}

export interface UpdateFlowJsonResult {
  action: 'update_whatsapp_flow_json';
  flowId: string;
  success: boolean;
  /** Meta validates on upload; these are why a publish would be refused. */
  validationErrors: string[];
  note: string;
}

export async function updateFlowJson(
  config: Config,
  input: UpdateFlowJsonInput,
): Promise<UpdateFlowJsonResult> {
  assertConfirmed(config, input.flowId, input.confirm);

  // Parsed here rather than passed through as an opaque string: a malformed
  // Flow JSON should fail locally with a clear message, not as an obscure
  // Meta rejection after a round-trip.
  let parsed: unknown;
  try {
    parsed = JSON.parse(input.flowJson);
  } catch (err) {
    throw new Error(`flowJson is not valid JSON: ${err instanceof Error ? err.message : String(err)}`);
  }
  const doc = parsed as { screens?: unknown[]; routing_model?: unknown };
  if (!Array.isArray(doc.screens) || doc.screens.length === 0) {
    throw new Error('flowJson has no "screens" array — refusing to upload a Flow with no screens.');
  }

  const res = (await graphPost(config, `${input.flowId}/assets`, {
    name: 'flow.json',
    asset_type: 'FLOW_JSON',
    file: input.flowJson,
  })) as { success?: boolean; validation_errors?: Array<{ error?: string; message?: string; error_type?: string }> };

  return {
    action: 'update_whatsapp_flow_json',
    flowId: input.flowId,
    success: res.success !== false,
    validationErrors: (res.validation_errors ?? [])
      .map((e) => [e.error_type, e.error ?? e.message].filter(Boolean).join(': '))
      .filter(Boolean)
      .slice(0, 20),
    note:
      'Uploading replaces the draft Flow JSON. It does NOT go live until publish_whatsapp_flow ' +
      'is called, and Meta refuses a publish while validation_errors is non-empty.',
  };
}

export interface PublishFlowResult {
  action: 'publish_whatsapp_flow';
  flowId: string;
  success: boolean;
  note: string;
}

export async function publishFlow(config: Config, input: PublishFlowInput): Promise<PublishFlowResult> {
  assertConfirmed(config, input.flowId, input.confirm);
  const res = (await graphPost(config, `${input.flowId}/publish`, {})) as { success?: boolean };
  return {
    action: 'publish_whatsapp_flow',
    flowId: input.flowId,
    success: res.success !== false,
    note:
      'The published Flow is now live for customers. A published Flow can no longer be edited ' +
      'in place — further changes need a new Flow version.',
  };
}

export interface DeprecateFlowResult {
  action: 'deprecate_whatsapp_flow';
  flowId: string;
  success: boolean;
  note: string;
}

export async function deprecateFlow(
  config: Config,
  input: DeprecateFlowInput,
): Promise<DeprecateFlowResult> {
  assertConfirmed(config, input.flowId, input.confirm);
  const res = (await graphPost(config, `${input.flowId}/deprecate`, {})) as { success?: boolean };
  return {
    action: 'deprecate_whatsapp_flow',
    flowId: input.flowId,
    success: res.success !== false,
    note:
      'IRREVERSIBLE. Customers already inside a session may continue, but no new session can ' +
      'open on this Flow. If this is the Flow the PIN or signup ladder uses, that journey is ' +
      'now broken until another Flow is published and WHATSAPP_FLOW_ID is repointed.',
  };
}

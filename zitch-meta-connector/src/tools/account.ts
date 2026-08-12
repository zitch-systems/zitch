/**
 * WABA-level and number-level configuration reads.
 *
 * Everything here describes ZITCH's own business setup — the account's review
 * status, the numbers it sends from, the public profile customers see on the
 * business. None of it is customer data: no conversations, no recipients, no
 * message content.
 */
import type { Config } from '../config.js';
import { graphGet } from '../metaClient.js';
import type {
  BusinessProfileInput,
  ConversationAnalyticsInput,
  ListPhoneNumbersInput,
  WabaDetailsInput,
} from '../schemas.js';

// ------------------------------------------------------------ WABA details
export interface WabaDetailsResult {
  wabaId: string;
  name: string | undefined;
  timezoneId: string | undefined;
  messageTemplateNamespace: string | undefined;
  /** Meta's review state for the account itself — the thing that silently
   * gates Flows, template approval and messaging limits. */
  accountReviewStatus: string | undefined;
  businessVerificationStatus: string | undefined;
  ownershipType: string | undefined;
  currency: string | undefined;
}

export async function getWabaDetails(config: Config, input: WabaDetailsInput): Promise<WabaDetailsResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const node = (await graphGet(config, wabaId, {
    fields:
      'id,name,timezone_id,message_template_namespace,account_review_status,' +
      'business_verification_status,ownership_type,currency',
  })) as Record<string, string | undefined>;
  return {
    wabaId,
    name: node.name,
    timezoneId: node.timezone_id,
    messageTemplateNamespace: node.message_template_namespace,
    accountReviewStatus: node.account_review_status,
    businessVerificationStatus: node.business_verification_status,
    ownershipType: node.ownership_type,
    currency: node.currency,
  };
}

// ------------------------------------------------------- phone number list
export interface ListPhoneNumbersResult {
  wabaId: string;
  phoneNumbers: Array<{
    id: string | undefined;
    displayPhoneNumber: string | undefined;
    verifiedName: string | undefined;
    qualityRating: string | undefined;
    codeVerificationStatus: string | undefined;
    messagingLimitTier: string | undefined;
    nameStatus: string | undefined;
    platformType: string | undefined;
  }>;
}

export async function listPhoneNumbers(
  config: Config,
  input: ListPhoneNumbersInput,
): Promise<ListPhoneNumbersResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const raw = (await graphGet(config, `${wabaId}/phone_numbers`, {
    fields:
      'id,display_phone_number,verified_name,quality_rating,code_verification_status,' +
      'messaging_limit_tier,name_status,platform_type',
    limit: 50,
  })) as { data?: Array<Record<string, string | undefined>> };
  return {
    wabaId,
    phoneNumbers: (raw.data ?? []).map((n) => ({
      id: n.id,
      displayPhoneNumber: n.display_phone_number,
      verifiedName: n.verified_name,
      qualityRating: n.quality_rating,
      codeVerificationStatus: n.code_verification_status,
      messagingLimitTier: n.messaging_limit_tier,
      nameStatus: n.name_status,
      platformType: n.platform_type,
    })),
  };
}

// ------------------------------------------------------- business profile
export interface BusinessProfileResult {
  phoneNumberId: string;
  about: string | undefined;
  address: string | undefined;
  description: string | undefined;
  email: string | undefined;
  websites: string[];
  vertical: string | undefined;
  profilePictureUrl: string | undefined;
}

export async function getBusinessProfile(
  config: Config,
  input: BusinessProfileInput,
): Promise<BusinessProfileResult> {
  const phoneNumberId = input.phoneNumberId ?? config.metaPhoneNumberId;
  const raw = (await graphGet(config, `${phoneNumberId}/whatsapp_business_profile`, {
    fields: 'about,address,description,email,websites,vertical,profile_picture_url',
  })) as { data?: Array<Record<string, unknown>> };
  // This edge returns a single-element `data` array rather than a bare node.
  const profile = (raw.data ?? [])[0] ?? {};
  return {
    phoneNumberId,
    about: profile.about as string | undefined,
    address: profile.address as string | undefined,
    description: profile.description as string | undefined,
    email: profile.email as string | undefined,
    websites: Array.isArray(profile.websites) ? (profile.websites as string[]) : [],
    vertical: profile.vertical as string | undefined,
    profilePictureUrl: profile.profile_picture_url as string | undefined,
  };
}

// -------------------------------------------------- conversation analytics
interface ConversationDataPoint {
  start?: number;
  end?: number;
  conversation?: number;
  cost?: number;
  conversation_category?: string;
  conversation_type?: string;
}

export interface ConversationAnalyticsResult {
  wabaId: string;
  windowHours: number;
  currency: string | undefined;
  totals: { conversations: number; cost: number };
  byCategory: Array<{ category: string; conversations: number; cost: number }>;
  dataPoints: number;
  note: string;
}

export async function getConversationAnalytics(
  config: Config,
  input: ConversationAnalyticsInput,
): Promise<ConversationAnalyticsResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const windowHours = input.lookbackHours ?? 24 * 7;
  const end = Math.floor(Date.now() / 1000);
  const start = end - windowHours * 3600;

  const raw = (await graphGet(config, wabaId, {
    fields:
      `conversation_analytics.start(${start}).end(${end}).granularity(DAILY)` +
      '.dimensions(["CONVERSATION_CATEGORY"])',
  })) as {
    conversation_analytics?: { data?: Array<{ data_points?: ConversationDataPoint[] }> };
  };

  const points = (raw.conversation_analytics?.data ?? []).flatMap((d) => d.data_points ?? []);
  const byCategory = new Map<string, { conversations: number; cost: number }>();
  let conversations = 0;
  let cost = 0;
  for (const point of points) {
    const category = point.conversation_category ?? 'UNKNOWN';
    const entry = byCategory.get(category) ?? { conversations: 0, cost: 0 };
    entry.conversations += point.conversation ?? 0;
    entry.cost += point.cost ?? 0;
    byCategory.set(category, entry);
    conversations += point.conversation ?? 0;
    cost += point.cost ?? 0;
  }

  return {
    wabaId,
    windowHours,
    currency: undefined,
    totals: { conversations, cost: Number(cost.toFixed(4)) },
    byCategory: [...byCategory.entries()]
      .map(([category, v]) => ({ category, conversations: v.conversations, cost: Number(v.cost.toFixed(4)) }))
      .sort((a, b) => b.conversations - a.conversations),
    dataPoints: points.length,
    note:
      'Aggregate conversation counts and billing only, grouped by category. No recipients, ' +
      'no message content, no per-customer detail — Meta does not expose those on this edge ' +
      'and this connector would not surface them if it did.',
  };
}

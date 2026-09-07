/**
 * Tool 2: check_whatsapp_phone_number_config
 *
 * Reads the *business's own* WhatsApp sending number configuration —
 * verification status, quality rating, messaging limit tier, display name
 * review status. This is operational config, not a customer's data: the
 * phone number here is Zitch's own outbound number.
 */
import type { Config } from '../config.js';
import { graphGet } from '../metaClient.js';
import type { CheckPhoneNumberConfigInput } from '../schemas.js';

const FIELDS = [
  'display_phone_number',
  'verified_name',
  'code_verification_status',
  'quality_rating',
  'messaging_limit_tier',
  'platform_type',
  'throughput',
  'is_official_business_account',
  'name_status',
].join(',');

export interface PhoneNumberConfigResult {
  phoneNumberId: string;
  displayPhoneNumber?: string;
  verifiedName?: string;
  codeVerificationStatus?: string;
  qualityRating?: string;
  messagingLimitTier?: string;
  platformType?: string;
  isOfficialBusinessAccount?: boolean;
  nameStatus?: string;
  throughput?: unknown;
}

interface PhoneNumberNode {
  display_phone_number?: string;
  verified_name?: string;
  code_verification_status?: string;
  quality_rating?: string;
  messaging_limit_tier?: string;
  platform_type?: string;
  is_official_business_account?: boolean;
  name_status?: string;
  throughput?: unknown;
}

export async function checkPhoneNumberConfig(
  config: Config,
  input: CheckPhoneNumberConfigInput,
): Promise<PhoneNumberConfigResult> {
  const phoneNumberId = input.phoneNumberId ?? config.metaPhoneNumberId;
  const node = (await graphGet(config, phoneNumberId, { fields: FIELDS })) as PhoneNumberNode;
  return {
    phoneNumberId,
    displayPhoneNumber: node.display_phone_number,
    verifiedName: node.verified_name,
    codeVerificationStatus: node.code_verification_status,
    qualityRating: node.quality_rating,
    messagingLimitTier: node.messaging_limit_tier,
    platformType: node.platform_type,
    isOfficialBusinessAccount: node.is_official_business_account,
    nameStatus: node.name_status,
    throughput: node.throughput,
  };
}

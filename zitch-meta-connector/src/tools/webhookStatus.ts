/**
 * Tool 1: check_whatsapp_webhook_status
 *
 * Meta's Graph API doesn't expose a "is the webhook currently reachable" ping
 * — webhooks are push-only. What it does expose, and what actually answers
 * "is our webhook wired up correctly", is the WABA's subscribed-apps list:
 * which app(s) are subscribed to this WABA's events. Some Graph API versions
 * also return `subscribed_fields`; others omit it entirely. Omission must stay
 * distinguishable from a real empty array or this diagnostic reports a false
 * outage while Meta is actively delivering events.
 */
import type { Config } from '../config.js';
import { graphGet } from '../metaClient.js';
import type { CheckWebhookStatusInput } from '../schemas.js';

interface SubscribedApp {
  whatsapp_business_api_data?: { id?: string; name?: string; link?: string };
  subscribed_fields?: string[];
}

interface SubscribedAppsResponse {
  data?: SubscribedApp[];
}

export interface WebhookStatusResult {
  wabaId: string;
  subscribed: boolean;
  fieldMetadataAvailable: boolean;
  subscribedApps: Array<{
    appName: string | undefined;
    subscribedFields: string[] | null;
  }>;
  note: string;
}

export async function checkWebhookStatus(
  config: Config,
  input: CheckWebhookStatusInput,
): Promise<WebhookStatusResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const raw = (await graphGet(config, `${wabaId}/subscribed_apps`)) as SubscribedAppsResponse;
  const apps = raw.data ?? [];
  const fieldMetadataAvailable = apps.every((app) => Array.isArray(app.subscribed_fields));
  return {
    wabaId,
    subscribed: apps.length > 0,
    fieldMetadataAvailable,
    subscribedApps: apps.map((app) => ({
      appName: app.whatsapp_business_api_data?.name,
      subscribedFields: Array.isArray(app.subscribed_fields) ? app.subscribed_fields : null,
    })),
    note: fieldMetadataAvailable
      ? 'Meta returned per-app field metadata for this request.'
      : 'Meta attached the listed app(s) to this WABA but omitted subscribed_fields. ' +
        'A null field list means unknown, not unsubscribed; confirm live delivery from ' +
        "Zitch's own webhook event history.",
  };
}

/**
 * Tool 1: check_whatsapp_webhook_status
 *
 * Meta's Graph API doesn't expose a "is the webhook currently reachable" ping
 * — webhooks are push-only. What it does expose, and what actually answers
 * "is our webhook wired up correctly", is the WABA's subscribed-apps list:
 * which app(s) are subscribed to this WABA's events, and — per app — which
 * event fields they're subscribed to. An empty list here is the single most
 * common real cause of "WhatsApp events stopped arriving".
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
  subscribedApps: Array<{ appName: string | undefined; subscribedFields: string[] }>;
}

export async function checkWebhookStatus(
  config: Config,
  input: CheckWebhookStatusInput,
): Promise<WebhookStatusResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const raw = (await graphGet(config, `${wabaId}/subscribed_apps`)) as SubscribedAppsResponse;
  const apps = raw.data ?? [];
  return {
    wabaId,
    subscribed: apps.length > 0,
    subscribedApps: apps.map((app) => ({
      appName: app.whatsapp_business_api_data?.name,
      subscribedFields: app.subscribed_fields ?? [],
    })),
  };
}

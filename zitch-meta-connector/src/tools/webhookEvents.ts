/**
 * Tool 5: inspect_webhook_events
 *
 * IMPORTANT LIMITATION (documented rather than papered over): Meta does not
 * provide a historical log of webhook deliveries via the Graph API — webhooks
 * are push-only and Meta keeps no replayable event history. What this tool
 * returns instead is the WABA's subscribed-app list and, when Meta includes
 * it, per-app field metadata. Graph API versions may omit
 * `subscribed_fields`; omission is reported as null rather than an empty array
 * so "unknown" can never be mistaken for "no events configured". Historical
 * delivery evidence still belongs to Zitch's production webhook event log.
 */
import type { Config } from '../config.js';
import { graphGet } from '../metaClient.js';
import type { InspectWebhookEventsInput } from '../schemas.js';

interface SubscribedApp {
  whatsapp_business_api_data?: { id?: string; name?: string };
  subscribed_fields?: string[];
}

interface SubscribedAppsResponse {
  data?: SubscribedApp[];
}

export interface WebhookEventsResult {
  wabaId: string;
  fieldMetadataAvailable: boolean;
  configuredEventTypes: string[] | null;
  perApp: Array<{ appName: string | undefined; fields: string[] | null }>;
  note: string;
}

export async function inspectWebhookEvents(
  config: Config,
  input: InspectWebhookEventsInput,
): Promise<WebhookEventsResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const raw = (await graphGet(config, `${wabaId}/subscribed_apps`)) as SubscribedAppsResponse;
  const apps = raw.data ?? [];
  const fieldMetadataAvailable = apps.every((app) => Array.isArray(app.subscribed_fields));
  const allFields = new Set<string>();
  for (const app of apps) {
    for (const field of app.subscribed_fields ?? []) allFields.add(field);
  }

  return {
    wabaId,
    fieldMetadataAvailable,
    configuredEventTypes: fieldMetadataAvailable ? [...allFields].sort() : null,
    perApp: apps.map((app) => ({
      appName: app.whatsapp_business_api_data?.name,
      fields: Array.isArray(app.subscribed_fields) ? app.subscribed_fields : null,
    })),
    note: fieldMetadataAvailable
      ? 'This reflects field metadata returned for the current subscribed app(s), not a ' +
        'historical delivery log.'
      : 'Meta returned the subscribed app(s) but omitted subscribed_fields. Null means ' +
        "unknown, not disabled; use Zitch's own webhook event history to prove delivery.",
  };
}

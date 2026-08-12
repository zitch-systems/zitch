/**
 * Tool 5: inspect_webhook_events
 *
 * IMPORTANT LIMITATION (documented rather than papered over): Meta does not
 * provide a historical log of webhook deliveries via the Graph API — webhooks
 * are push-only and Meta keeps no replayable event history. What this tool
 * returns instead is the WABA's current webhook *subscription configuration*:
 * which event types (fields) each subscribed app is currently set up to
 * receive — e.g. `messages`, `message_template_status_update`,
 * `account_update`. That answers "what events are we configured to get",
 * which is the useful maintenance question when messages/templates aren't
 * updating as expected; it is not a log of specific past deliveries. If Zitch
 * wants true webhook-event history, that has to come from the backend's own
 * event log via a dedicated read-only ops endpoint — deliberately out of
 * scope here, since this connector does not touch the production webhook.
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
  configuredEventTypes: string[];
  perApp: Array<{ appName: string | undefined; fields: string[] }>;
  note: string;
}

export async function inspectWebhookEvents(
  config: Config,
  input: InspectWebhookEventsInput,
): Promise<WebhookEventsResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const raw = (await graphGet(config, `${wabaId}/subscribed_apps`)) as SubscribedAppsResponse;
  const apps = raw.data ?? [];
  const allFields = new Set<string>();
  for (const app of apps) {
    for (const field of app.subscribed_fields ?? []) allFields.add(field);
  }

  return {
    wabaId,
    configuredEventTypes: [...allFields].sort(),
    perApp: apps.map((app) => ({
      appName: app.whatsapp_business_api_data?.name,
      fields: app.subscribed_fields ?? [],
    })),
    note:
      'This reflects current webhook SUBSCRIPTION CONFIGURATION (which event types are ' +
      'enabled), not a historical log of past webhook deliveries — Meta\'s Graph API does not ' +
      'expose the latter.',
  };
}

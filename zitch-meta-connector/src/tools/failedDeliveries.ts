/**
 * Tool 4: inspect_failed_message_deliveries
 *
 * IMPORTANT LIMITATION (documented rather than papered over): Meta's Graph
 * API does not expose a queryable list of individual failed messages —
 * delivery failures arrive only as real-time `statuses` webhook events, which
 * Meta does not retain or let you replay. What the Graph API DOES expose is
 * WABA-level aggregate analytics (messages sent vs. delivered per day), which
 * is what this tool returns: a daily breakdown with an approximate
 * `undelivered` count (sent − delivered). This is aggregate and numeric only
 * — no phone numbers, no message content, no per-customer detail — so it
 * carries no customer PII, but it is a health signal, not an incident log.
 * A true per-message failure log would have to come from Zitch's own
 * webhook-event storage, which this connector deliberately does not touch
 * (see README — this project does not modify the production webhook).
 */
import type { Config } from '../config.js';
import { graphGet } from '../metaClient.js';
import type { InspectFailedDeliveriesInput } from '../schemas.js';

interface AnalyticsDataPoint {
  start?: number;
  end?: number;
  sent?: number;
  delivered?: number;
}

interface AnalyticsField {
  granularity?: string;
  data_points?: AnalyticsDataPoint[];
}

interface WabaAnalyticsNode {
  analytics?: AnalyticsField;
}

export interface FailedDeliveriesResult {
  wabaId: string;
  windowHours: number;
  granularity: string | undefined;
  dailyBreakdown: Array<{
    periodStart: string;
    periodEnd: string;
    sent: number;
    delivered: number;
    undeliveredApprox: number;
  }>;
  note: string;
}

export async function inspectFailedDeliveries(
  config: Config,
  input: InspectFailedDeliveriesInput,
): Promise<FailedDeliveriesResult> {
  const wabaId = input.wabaId ?? config.metaWabaId;
  const windowHours = input.lookbackHours ?? 24;
  const endSeconds = Math.floor(Date.now() / 1000);
  const startSeconds = endSeconds - windowHours * 3600;

  const raw = (await graphGet(config, wabaId, {
    fields: `analytics.start(${startSeconds}).end(${endSeconds}).granularity(DAY)`,
  })) as WabaAnalyticsNode;

  const points = raw.analytics?.data_points ?? [];

  return {
    wabaId,
    windowHours,
    granularity: raw.analytics?.granularity,
    dailyBreakdown: points.map((p) => {
      const sent = p.sent ?? 0;
      const delivered = p.delivered ?? 0;
      return {
        periodStart: p.start ? new Date(p.start * 1000).toISOString() : '',
        periodEnd: p.end ? new Date(p.end * 1000).toISOString() : '',
        sent,
        delivered,
        undeliveredApprox: Math.max(0, sent - delivered),
      };
    }),
    note:
      'undeliveredApprox = sent - delivered per Meta\'s aggregate WABA analytics. Meta does ' +
      'not provide a per-message failure list or error-code breakdown via the Graph API; a ' +
      'precise incident log would require Zitch\'s own webhook-event history.',
  };
}

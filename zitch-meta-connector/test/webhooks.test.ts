import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Config } from '../src/config.js';
import { inspectWebhookEvents } from '../src/tools/webhookEvents.js';
import { checkWebhookStatus } from '../src/tools/webhookStatus.js';

const config = {
  graphApiBaseUrl: 'https://graph.facebook.test',
  graphApiVersion: 'v26.0',
  graphTimeoutMs: 1_000,
  metaAccessToken: 'never-return-this-token',
  metaWabaId: '1352678890019106',
} as Config;

function graphResponse(data: unknown): Response {
  return new Response(JSON.stringify({ data }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('WhatsApp webhook subscription diagnostics', () => {
  it('does not turn omitted field metadata into an empty subscription', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => graphResponse([
      { whatsapp_business_api_data: { id: 'app-1', name: 'Zitch Banking' } },
    ])));

    const status = await checkWebhookStatus(config, {});
    const events = await inspectWebhookEvents(config, {});

    expect(status.subscribed).toBe(true);
    expect(status.fieldMetadataAvailable).toBe(false);
    expect(status.subscribedApps[0]?.subscribedFields).toBeNull();
    expect(events.fieldMetadataAvailable).toBe(false);
    expect(events.configuredEventTypes).toBeNull();
    expect(events.perApp[0]?.fields).toBeNull();
    expect(events.note).toContain('unknown, not disabled');
  });

  it('returns and aggregates field metadata when Meta supplies it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => graphResponse([
      {
        whatsapp_business_api_data: { id: 'app-1', name: 'Zitch Banking' },
        subscribed_fields: ['messages', 'account_update'],
      },
      {
        whatsapp_business_api_data: { id: 'app-2', name: 'Operations' },
        subscribed_fields: ['messages', 'message_template_status_update'],
      },
    ])));

    const status = await checkWebhookStatus(config, {});
    const events = await inspectWebhookEvents(config, {});

    expect(status.fieldMetadataAvailable).toBe(true);
    expect(status.subscribedApps[0]?.subscribedFields).toEqual(['messages', 'account_update']);
    expect(events.configuredEventTypes).toEqual([
      'account_update',
      'message_template_status_update',
      'messages',
    ]);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { _resetConfigForTests, loadConfig, type Config } from '../src/config.js';
import {
  createMessageTemplateSchema,
  deleteMessageTemplateSchema,
  deprecateFlowSchema,
  publishFlowSchema,
  updateBusinessProfileSchema,
  updateFlowJsonSchema,
} from '../src/schemas.js';
import { TOOL_REGISTRY, targetOf, toolsFor } from '../src/tools/registry.js';
import {
  ConfirmationError,
  deprecateFlow,
  publishFlow,
  updateBusinessProfile,
  updateFlowJson,
} from '../src/tools/writes.js';

function writableConfig(overrides: Partial<Config> = {}): Config {
  return {
    metaAccessToken: 'meta-token',
    metaWabaId: '111',
    metaPhoneNumberId: '222',
    metaAppId: undefined,
    metaAppSecret: undefined,
    connectorApiKey: 'a'.repeat(32),
    port: 0,
    graphApiVersion: 'v21.0',
    graphApiBaseUrl: 'https://graph.facebook.com',
    graphTimeoutMs: 1000,
    rateLimitWindowMs: 60_000,
    rateLimitMaxRequests: 100,
    ipRateLimitMaxRequests: 100,
    readOnly: false,
    requireWriteConfirmation: true,
    publicBaseUrl: undefined,
    oauthSigningKey: 'k'.repeat(32),
    oauthLoginPassword: 'p'.repeat(32),
    oauthAllowedRedirectHosts: ['claude.ai'],
    oauthStaticClient: undefined,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('write-mode gating', () => {
  it('hides every write tool in read-only mode', () => {
    const names = toolsFor(writableConfig({ readOnly: true })).map((t) => t.name);
    expect(names).not.toContain('publish_whatsapp_flow');
    expect(names).not.toContain('deprecate_whatsapp_flow');
    expect(names).not.toContain('delete_message_template');
    // Reads are unaffected.
    expect(names).toContain('list_whatsapp_flows');
  });

  it('exposes them when writes are enabled', () => {
    const names = toolsFor(writableConfig()).map((t) => t.name);
    expect(names).toContain('publish_whatsapp_flow');
    expect(names).toContain('update_whatsapp_flow_json');
  });

  it('refuses a write call outright when the connector is read-only', async () => {
    // Defence in depth: even if a write tool were somehow invoked on a
    // read-only instance, the Graph client itself refuses.
    await expect(
      publishFlow(writableConfig({ readOnly: true }), { flowId: '123', confirm: '123' }),
    ).rejects.toThrow(/read-only/i);
  });
});

describe('the confirmation interlock', () => {
  const config = writableConfig();

  it('refuses when confirm does not match the target', async () => {
    await expect(publishFlow(config, { flowId: '123', confirm: '456' })).rejects.toBeInstanceOf(
      ConfirmationError,
    );
    await expect(
      deprecateFlow(config, { flowId: '999', confirm: 'the other flow' }),
    ).rejects.toBeInstanceOf(ConfirmationError);
  });

  it('names both the expected and supplied value so the mistake is obvious', async () => {
    await expect(publishFlow(config, { flowId: '123', confirm: '456' })).rejects.toThrow(
      /would change "123".*confirm was "456"/s,
    );
  });

  it('refuses a near-miss, not just an empty confirm', async () => {
    // The realistic failure is an AI restating something *close* to the
    // target, not omitting it.
    await expect(publishFlow(config, { flowId: '1234567', confirm: '123456' })).rejects.toBeInstanceOf(
      ConfirmationError,
    );
  });

  it('can be disabled deliberately, and then does not block', async () => {
    // With the interlock off the call proceeds past confirmation and fails
    // later at the network boundary instead — proving confirmation is what
    // was stopping it before.
    const relaxed = writableConfig({ requireWriteConfirmation: false });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      error: { message: 'simulated upstream refusal' },
    }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    })));
    await expect(
      publishFlow(relaxed, { flowId: '123', confirm: 'totally-wrong' }),
    ).rejects.not.toBeInstanceOf(ConfirmationError);
  });
});

describe('how a refused write is reported', () => {
  // Regression: the interlock originally surfaced as a bare 500 "Internal
  // error", which told the caller nothing about what to fix — defeating the
  // point of a guardrail that exists to be corrected.
  it('is a distinct, actionable error type rather than a generic failure', async () => {
    const err = await publishFlow(writableConfig(), { flowId: '123', confirm: 'no' }).catch((e) => e);
    expect(err).toBeInstanceOf(ConfirmationError);
    expect(err.message).toContain('123');
    expect(err.message).toMatch(/re-issue/i);
  });

  it('leaks no secret in that message', async () => {
    const config = writableConfig();
    const err = await publishFlow(config, { flowId: '123', confirm: 'no' }).catch((e) => e);
    expect(err.message).not.toContain(config.metaAccessToken);
    expect(err.message).not.toContain(config.connectorApiKey);
  });
});

describe('write input validation', () => {
  it('requires confirm on every write schema', () => {
    expect(() => publishFlowSchema.parse({ flowId: '123' })).toThrow();
    expect(() => deprecateFlowSchema.parse({ flowId: '123' })).toThrow();
    expect(() => deleteMessageTemplateSchema.parse({ name: 'x' })).toThrow();
  });

  it('enforces Meta template naming rules', () => {
    const base = {
      language: 'en_US',
      category: 'UTILITY' as const,
      components: [{ type: 'BODY', text: 'hi' }],
      confirm: 'x',
    };
    expect(() => createMessageTemplateSchema.parse({ ...base, name: 'Bad Name' })).toThrow();
    expect(() => createMessageTemplateSchema.parse({ ...base, name: 'good_name_1' })).not.toThrow();
  });

  it('rejects an unknown template category', () => {
    expect(() =>
      createMessageTemplateSchema.parse({
        name: 'x',
        language: 'en_US',
        category: 'ANYTHING',
        components: [{ type: 'BODY' }],
        confirm: 'x',
      }),
    ).toThrow();
  });

  it('bounds the business profile fields Meta itself bounds', () => {
    expect(() =>
      updateBusinessProfileSchema.parse({ about: 'x'.repeat(140), confirm: '222' }),
    ).toThrow();
    expect(() =>
      updateBusinessProfileSchema.parse({ email: 'not-an-email', confirm: '222' }),
    ).toThrow();
    expect(() =>
      updateBusinessProfileSchema.parse({ websites: ['a', 'b', 'c'], confirm: '222' }),
    ).toThrow();
  });

  it('accepts a Flow JSON large enough for the real pin_flow.json', () => {
    // The real document is ~329KB because the logo is inlined as base64; a
    // tighter bound would reject the actual production Flow.
    const big = JSON.stringify({ screens: [{ id: 'A' }], padding: 'x'.repeat(300_000) });
    expect(() => updateFlowJsonSchema.parse({ flowId: '1', flowJson: big, confirm: '1' })).not.toThrow();
  });
});

describe('Flow JSON upload validation', () => {
  const config = writableConfig();

  it('rejects malformed JSON locally, with a clear message', async () => {
    await expect(
      updateFlowJson(config, { flowId: '1', flowJson: '{not json', confirm: '1' }),
    ).rejects.toThrow(/not valid JSON/);
  });

  it('refuses a Flow with no screens rather than uploading it', async () => {
    await expect(
      updateFlowJson(config, { flowId: '1', flowJson: '{"screens":[]}', confirm: '1' }),
    ).rejects.toThrow(/no "screens"/);
  });

  it('checks confirmation before it even parses the document', async () => {
    await expect(
      updateFlowJson(config, { flowId: '1', flowJson: '{not json', confirm: 'wrong' }),
    ).rejects.toBeInstanceOf(ConfirmationError);
  });

  it('uploads a real multipart file instead of a false-success JSON body', async () => {
    const flowJson = JSON.stringify({ screens: [{ id: 'A' }], routing_model: {} });
    const fetchMock = vi.fn(async (_url: URL, init?: RequestInit) => {
      expect(init?.method).toBe('POST');
      expect(init?.body).toBeInstanceOf(FormData);

      const headers = new Headers(init?.headers);
      // fetch must add the boundary generated for this FormData instance.
      expect(headers.has('content-type')).toBe(false);

      const form = init?.body as FormData;
      expect(form.get('name')).toBe('flow.json');
      expect(form.get('asset_type')).toBe('FLOW_JSON');
      const file = form.get('file');
      expect(file).toBeInstanceOf(Blob);
      expect((file as Blob & { name?: string }).name).toBe('flow.json');
      expect(await (file as Blob).text()).toBe(flowJson);

      return new Response(JSON.stringify({ success: true, validation_errors: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      updateFlowJson(config, { flowId: '123', flowJson, confirm: '123' }),
    ).resolves.toMatchObject({ success: true, validationErrors: [] });
    expect(fetchMock).toHaveBeenCalledOnce();
  });
});

describe('audit targeting', () => {
  it('identifies the resource a write is aimed at', () => {
    expect(targetOf({ flowId: '123' })).toBe('flowId=123');
    expect(targetOf({ name: 'welcome_v2' })).toBe('name=welcome_v2');
    expect(targetOf({ phoneNumberId: '999' })).toBe('phoneNumberId=999');
  });

  it('returns undefined when nothing identifies the target', () => {
    expect(targetOf({})).toBeUndefined();
  });

  it('bounds the logged value', () => {
    expect(targetOf({ name: 'x'.repeat(500) })!.length).toBeLessThan(140);
  });
});

describe('META_ALLOW_WRITES config', () => {
  const saved: Record<string, string | undefined> = {};
  const KEYS = [
    'META_ACCESS_TOKEN',
    'META_WABA_ID',
    'META_PHONE_NUMBER_ID',
    'CONNECTOR_API_KEY',
    'META_ALLOW_WRITES',
    'META_REQUIRE_WRITE_CONFIRMATION',
  ];

  beforeEach(() => {
    for (const k of KEYS) {
      saved[k] = process.env[k];
      delete process.env[k];
    }
    process.env.META_ACCESS_TOKEN = 't';
    process.env.META_WABA_ID = '1';
    process.env.META_PHONE_NUMBER_ID = '2';
    process.env.CONNECTOR_API_KEY = 'a'.repeat(32);
    _resetConfigForTests();
  });

  afterEach(() => {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
    _resetConfigForTests();
  });

  it('is read-only by default', () => {
    expect(loadConfig().readOnly).toBe(true);
  });

  it('enables writes only on an explicit affirmative', () => {
    process.env.META_ALLOW_WRITES = 'true';
    expect(loadConfig().readOnly).toBe(false);
  });

  it('requires write confirmation by default', () => {
    expect(loadConfig().requireWriteConfirmation).toBe(true);
  });

  it('refuses an ambiguous value rather than guessing', () => {
    // A typo must never silently switch writes on — or, worse, read as
    // truthy. It fails at boot instead.
    process.env.META_ALLOW_WRITES = 'ture';
    expect(() => loadConfig()).toThrow(/true\/false/);
  });

  it('treats an explicit false as read-only', () => {
    process.env.META_ALLOW_WRITES = 'false';
    expect(loadConfig().readOnly).toBe(true);
  });
});

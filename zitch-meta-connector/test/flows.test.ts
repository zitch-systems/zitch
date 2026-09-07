import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

import { isAllowedAssetUrl } from '../src/metaClient.js';
import {
  conversationAnalyticsSchema,
  inspectFlowSchema,
  listFlowsSchema,
  publishedFlowScreensSchema,
} from '../src/schemas.js';
import { TOOL_REGISTRY, toolsFor } from '../src/tools/registry.js';
import { analyseFlowJson } from '../src/tools/flows.js';

describe('published Flow analysis', () => {
  // Modelled on the real pin_flow.json: a screen that can be OPENED directly
  // (no incoming routes) versus one that can only be chained into. Getting
  // this wrong is the 131009 "screen not allowed as first screen" rejection.
  const flow = {
    version: '5.1',
    data_api_version: '3.0',
    routing_model: {
      TRANSFER_FORM: ['PIN_CHAIN', 'SUCCESS'],
      PIN_SCREEN: ['PIN_CONFIRM', 'SUCCESS'],
      PIN_CHAIN: ['PIN_CONFIRM'],
      PIN_CONFIRM: ['SUCCESS'],
    },
    screens: [
      { id: 'TRANSFER_FORM' },
      { id: 'PIN_SCREEN' },
      { id: 'PIN_CHAIN' },
      { id: 'PIN_CONFIRM' },
      { id: 'SUCCESS', terminal: true },
      { id: 'ORPHAN_SCREEN' },
    ],
  };

  it('identifies exactly the screens a Flow may open on', () => {
    const result = analyseFlowJson('123', flow);
    // TRANSFER_FORM and PIN_SCREEN have no incoming routes -> openable.
    // PIN_CHAIN is chained into -> NOT openable, which is precisely why the
    // twin exists alongside PIN_SCREEN.
    expect(result.routingRootScreens).toContain('TRANSFER_FORM');
    expect(result.routingRootScreens).toContain('PIN_SCREEN');
    expect(result.routingRootScreens).not.toContain('PIN_CHAIN');
    expect(result.routingRootScreens).not.toContain('SUCCESS');
  });

  it('reports terminal screens', () => {
    expect(analyseFlowJson('123', flow).terminalScreens).toEqual(['SUCCESS']);
  });

  it('flags a screen that is defined but never reachable', () => {
    expect(analyseFlowJson('123', flow).unreachableScreens).toContain('ORPHAN_SCREEN');
  });

  it('flags a route pointing at a screen that does not exist', () => {
    const broken = {
      ...flow,
      routing_model: { ...flow.routing_model, TRANSFER_FORM: ['TYPO_SCREEN'] },
    };
    expect(analyseFlowJson('123', broken).danglingRoutes).toContain('TYPO_SCREEN');
  });

  it('survives a Flow with no routing model at all', () => {
    const bare = { screens: [{ id: 'ONLY' }] };
    const result = analyseFlowJson('123', bare);
    expect(result.screens).toEqual(['ONLY']);
    expect(result.routingRootScreens).toEqual(['ONLY']);
    expect(result.danglingRoutes).toEqual([]);
  });

  it('survives an empty document without throwing', () => {
    expect(() => analyseFlowJson('123', {})).not.toThrow();
    expect(analyseFlowJson('123', {}).screens).toEqual([]);
  });
});

describe('Flow asset host allowlist', () => {
  // The Flow JSON is not on the Graph node — the API returns a signed CDN URL
  // and we follow it. A response value deciding where we send a request is the
  // shape of an SSRF, so the host is pinned.

  it('allows Meta CDN hosts', () => {
    for (const url of [
      'https://lookaside.fbsbx.com/whatsapp_business/flows/123',
      'https://scontent-lhr8-1.xx.fbcdn.net/v/t39/flow.json',
      'https://graph.facebook.com/v21.0/asset',
      'https://media.whatsapp.net/flow.json',
    ]) {
      expect(isAllowedAssetUrl(url), url).toBe(true);
    }
  });

  it('refuses non-Meta hosts', () => {
    for (const url of [
      'https://evil.test/flow.json',
      'https://attacker.example.com/x',
      'https://169.254.169.254/latest/meta-data/', // cloud metadata endpoint
      'http://localhost:8787/admin',
    ]) {
      expect(isAllowedAssetUrl(url), url).toBe(false);
    }
  });

  it('refuses a look-alike host that merely ends with the same letters', () => {
    // The leading dot in the suffix list is what makes this fail; a plain
    // endsWith('fbcdn.net') would accept it.
    expect(isAllowedAssetUrl('https://evil-fbcdn.net/flow.json')).toBe(false);
    expect(isAllowedAssetUrl('https://notfacebook.com/flow.json')).toBe(false);
  });

  it('refuses plain http even on a Meta host', () => {
    expect(isAllowedAssetUrl('http://lookaside.fbsbx.com/flow.json')).toBe(false);
  });

  it('refuses a malformed URL without throwing', () => {
    expect(() => isAllowedAssetUrl('not a url')).not.toThrow();
    expect(isAllowedAssetUrl('not a url')).toBe(false);
  });
});

describe('new tool schemas', () => {
  it('requires a flowId for the per-Flow tools', () => {
    expect(() => inspectFlowSchema.parse({})).toThrow();
    expect(() => publishedFlowScreensSchema.parse({})).toThrow();
    expect(() => inspectFlowSchema.parse({ flowId: '123456789' })).not.toThrow();
  });

  it('rejects a non-numeric flowId', () => {
    expect(() => inspectFlowSchema.parse({ flowId: '../../etc/passwd' })).toThrow();
    expect(() => inspectFlowSchema.parse({ flowId: '123/assets?x=1' })).toThrow();
  });

  it('bounds the flow list page size', () => {
    expect(() => listFlowsSchema.parse({ limit: 101 })).toThrow();
    expect(() => listFlowsSchema.parse({ limit: 50 })).not.toThrow();
  });

  it('bounds the conversation-analytics lookback to 30 days', () => {
    expect(() => conversationAnalyticsSchema.parse({ lookbackHours: 721 })).toThrow();
    expect(() => conversationAnalyticsSchema.parse({ lookbackHours: 720 })).not.toThrow();
  });

  it('rejects unknown fields on every new schema', () => {
    expect(() => listFlowsSchema.parse({ nope: 1 })).toThrow();
    expect(() => conversationAnalyticsSchema.parse({ nope: 1 })).toThrow();
  });
});

describe('tool registry', () => {
  it('exposes every tool with a unique snake_case name', () => {
    const names = TOOL_REGISTRY.map((t) => t.name);
    expect(new Set(names).size).toBe(names.length);
    for (const name of names) expect(name).toMatch(/^[a-z][a-z0-9_]*$/);
  });

  it('includes the Flow tools', () => {
    const names = TOOL_REGISTRY.map((t) => t.name);
    expect(names).toContain('list_whatsapp_flows');
    expect(names).toContain('inspect_whatsapp_flow');
    expect(names).toContain('get_published_flow_screens');
  });

  it('gives every tool a title, description and schema', () => {
    for (const tool of TOOL_REGISTRY) {
      expect(tool.title, tool.name).toBeTruthy();
      expect(tool.description.length, tool.name).toBeGreaterThan(40);
      expect(tool.schema, tool.name).toBeDefined();
      expect(typeof tool.handler, tool.name).toBe('function');
    }
  });

  it('has an openapi.yaml path for every tool', () => {
    // The REST mirror and the spec are generated from different places, so
    // they drift silently: add a tool, forget the spec, and a GPT Action
    // simply never sees it. Regex rather than a YAML parser to avoid pulling
    // in a dependency just for this check.
    const spec = readFileSync(new URL('../openapi.yaml', import.meta.url), 'utf8');
    const declared = new Set(
      [...spec.matchAll(/^ {2}(\/[a-z0-9-]*):/gm)].map((m) => m[1]),
    );
    for (const { name } of TOOL_REGISTRY) {
      const path = `/${name.replace(/_/g, '-')}`;
      expect(declared.has(path), `openapi.yaml is missing ${path}`).toBe(true);
    }
  });

  it('flags every mutating-sounding tool as write: true', () => {
    // The guardrail that matters now writes exist: a tool named like a
    // mutation MUST be marked `write`, because that flag is what keeps it out
    // of read-only mode. An unflagged write tool would be exposed even on a
    // read-only instance.
    for (const tool of TOOL_REGISTRY) {
      if (/^(create|update|delete|send|post|set|rotate|revoke|pay|publish|deprecate)_/.test(tool.name)) {
        expect(tool.write, `${tool.name} looks like a write but is not flagged write:true`).toBe(true);
      }
    }
  });

  it('never marks a read-sounding tool as a write, or vice versa', () => {
    for (const tool of TOOL_REGISTRY) {
      if (/^(list|get|check|inspect|verify)_/.test(tool.name)) {
        expect(tool.write, `${tool.name} reads but is flagged write:true`).toBeFalsy();
      }
    }
  });

  it('exposes NO write tool in read-only mode', () => {
    const readOnly = toolsFor({ readOnly: true } as never);
    expect(readOnly.some((t) => t.write)).toBe(false);
    // ...and every one of them is back when writes are enabled.
    const writable = toolsFor({ readOnly: false } as never);
    expect(writable.length).toBeGreaterThan(readOnly.length);
    expect(writable.filter((t) => t.write).length).toBe(TOOL_REGISTRY.filter((t) => t.write).length);
  });

  it('requires a confirm argument on every write tool', () => {
    // The interlock only works if it is on the schema; a write tool without
    // `confirm` would silently skip it.
    for (const tool of TOOL_REGISTRY.filter((t) => t.write)) {
      expect(Object.keys(tool.schema.shape), `${tool.name} has no confirm field`).toContain('confirm');
    }
  });

  it('exposes no message-sending tool at all', () => {
    // The hard line: this connector must never be able to send a WhatsApp
    // message from Zitch's verified number, in any mode.
    for (const { name } of TOOL_REGISTRY) {
      expect(name, name).not.toMatch(/send|message_send|broadcast|notify/);
    }
  });

  it('exposes no tool that can change webhook subscriptions', () => {
    // Unsubscribing the WABA would stop the live banking channel processing
    // customer messages, with no error surfaced anywhere.
    for (const { name } of TOOL_REGISTRY) {
      expect(name, name).not.toMatch(/subscribe|unsubscribe/);
    }
  });
});

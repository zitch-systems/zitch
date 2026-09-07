import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { JSON_BODY_LIMIT_BYTES, MAX_FLOW_JSON_CHARS } from '../src/httpLimits.js';
import { updateFlowJsonSchema } from '../src/schemas.js';

const FLOW_ASSET = fileURLToPath(
  new URL('../../backend/whatsapp/flow_assets/pin_flow.json', import.meta.url),
);

describe('Flow upload transport limit', () => {
  it('accepts the checked-in Flow through its complete JSON-RPC envelope', () => {
    const flowJson = readFileSync(FLOW_ASSET, 'utf8');
    const request = JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: {
        name: 'update_whatsapp_flow_json',
        arguments: { flowId: '123', confirm: '123', flowJson },
      },
    });

    // Proves this fixture catches the old 256-KB production limit.
    expect(Buffer.byteLength(request)).toBeGreaterThan(256 * 1024);
    expect(flowJson.length).toBeLessThanOrEqual(MAX_FLOW_JSON_CHARS);
    expect(Buffer.byteLength(request)).toBeLessThanOrEqual(JSON_BODY_LIMIT_BYTES);
    expect(() => updateFlowJsonSchema.parse({ flowId: '123', confirm: '123', flowJson }))
      .not.toThrow();
  });

  it('keeps oversized Flow strings bounded before an outbound Meta call', () => {
    const tooLarge = JSON.stringify({
      screens: [{ id: 'A' }],
      padding: 'x'.repeat(MAX_FLOW_JSON_CHARS),
    });
    expect(tooLarge.length).toBeGreaterThan(MAX_FLOW_JSON_CHARS);
    expect(() => updateFlowJsonSchema.parse({ flowId: '123', confirm: '123', flowJson: tooLarge }))
      .toThrow();
  });
});

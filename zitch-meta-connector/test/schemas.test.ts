import { describe, expect, it } from 'vitest';

import {
  checkPhoneNumberConfigSchema,
  checkWebhookStatusSchema,
  inspectFailedDeliveriesSchema,
  inspectWebhookEventsSchema,
  listMessageTemplatesSchema,
  verifyMetaCredentialsSchema,
} from '../src/schemas.js';

describe('checkWebhookStatusSchema', () => {
  it('accepts a numeric waba id', () => {
    expect(() => checkWebhookStatusSchema.parse({ wabaId: '123456789' })).not.toThrow();
  });

  it('accepts an omitted waba id', () => {
    expect(() => checkWebhookStatusSchema.parse({})).not.toThrow();
  });

  it('rejects a non-numeric waba id', () => {
    expect(() => checkWebhookStatusSchema.parse({ wabaId: 'abc' })).toThrow();
  });

  it('rejects an injection-shaped id', () => {
    expect(() => checkWebhookStatusSchema.parse({ wabaId: '1; DROP TABLE users' })).toThrow();
  });

  it('rejects unknown extra fields', () => {
    expect(() => checkWebhookStatusSchema.parse({ wabaId: '1', extra: 'nope' })).toThrow();
  });
});

describe('checkPhoneNumberConfigSchema', () => {
  it('accepts a numeric phone number id', () => {
    expect(() =>
      checkPhoneNumberConfigSchema.parse({ phoneNumberId: '987654321' }),
    ).not.toThrow();
  });

  it('rejects a non-numeric id', () => {
    expect(() => checkPhoneNumberConfigSchema.parse({ phoneNumberId: 'not-a-number' })).toThrow();
  });
});

describe('listMessageTemplatesSchema', () => {
  it('accepts a valid limit and cursor', () => {
    expect(() =>
      listMessageTemplatesSchema.parse({ limit: 50, after: 'cursor123' }),
    ).not.toThrow();
  });

  it('rejects a limit above 100', () => {
    expect(() => listMessageTemplatesSchema.parse({ limit: 500 })).toThrow();
  });

  it('rejects a limit of zero', () => {
    expect(() => listMessageTemplatesSchema.parse({ limit: 0 })).toThrow();
  });

  it('rejects a non-integer limit', () => {
    expect(() => listMessageTemplatesSchema.parse({ limit: 1.5 })).toThrow();
  });
});

describe('inspectFailedDeliveriesSchema', () => {
  it('accepts a lookback within 30 days', () => {
    expect(() => inspectFailedDeliveriesSchema.parse({ lookbackHours: 720 })).not.toThrow();
  });

  it('rejects a lookback beyond 30 days', () => {
    expect(() => inspectFailedDeliveriesSchema.parse({ lookbackHours: 721 })).toThrow();
  });

  it('rejects a zero or negative lookback', () => {
    expect(() => inspectFailedDeliveriesSchema.parse({ lookbackHours: 0 })).toThrow();
    expect(() => inspectFailedDeliveriesSchema.parse({ lookbackHours: -5 })).toThrow();
  });
});

describe('inspectWebhookEventsSchema', () => {
  it('accepts an omitted waba id', () => {
    expect(() => inspectWebhookEventsSchema.parse({})).not.toThrow();
  });
});

describe('verifyMetaCredentialsSchema', () => {
  it('accepts an empty object (no arguments needed)', () => {
    expect(() => verifyMetaCredentialsSchema.parse({})).not.toThrow();
  });

  it('rejects unexpected arguments', () => {
    expect(() => verifyMetaCredentialsSchema.parse({ unexpected: true })).toThrow();
  });
});

/**
 * Structured audit log — one JSON line per tool/REST invocation, to stdout.
 *
 * Stdout rather than a file: this connector is meant to run on a platform
 * (Render, Fly, a container host) whose log pipeline already collects stdout,
 * and a file on ephemeral/container storage is not durable anyway. Every
 * field here is safe to store and forward: caller fingerprint (never the raw
 * key), tool name, outcome, latency, and a redacted error message. Nothing
 * from the Meta response body is logged — only whether the call succeeded —
 * since even "safe" config data doesn't need to sit in a log store forever.
 */
import { redactDeep } from './redact.js';

export interface AuditEvent {
  tool: string;
  keyFingerprint: string;
  ip: string | undefined;
  /** Which credential type the caller used — 'api_key' (a direct
   * CONNECTOR_API_KEY holder, e.g. a script or health probe) or 'oauth' (a
   * browser-authorized client such as Claude). Worth recording separately:
   * "was that call a person in Claude, or an automated key holder?" is the
   * first question anyone asks of one of these log lines. */
  authMethod?: string;
  /** True when this call MUTATED Meta configuration. Logged as its own field
   * rather than left implicit in the tool name so the write history can be
   * pulled out of a log store with one filter — "what changed, when, and on
   * whose credential" is the question an incident review starts from. */
  write?: boolean;
  /** For writes: the exact resource that was changed (template name, Flow ID,
   * phone number ID). Never a secret — these are Meta object identifiers. */
  target?: string;
  outcome: 'success' | 'error' | 'denied';
  durationMs: number;
  errorMessage?: string;
}

export function auditLog(event: AuditEvent): void {
  const record = redactDeep({
    at: new Date().toISOString(),
    level: 'audit',
    ...event,
  });
  // One line, machine-parseable, matches the rest of the platform's log shape.
  process.stdout.write(`${JSON.stringify(record)}\n`);
}

/** Wraps a tool/REST handler so every call is timed and logged exactly once,
 * on both the success and failure path. */
export async function withAudit<T>(
  meta: { tool: string; keyFingerprint: string; ip: string | undefined },
  fn: () => Promise<T>,
): Promise<T> {
  const start = Date.now();
  try {
    const result = await fn();
    auditLog({ ...meta, outcome: 'success', durationMs: Date.now() - start });
    return result;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    auditLog({
      ...meta,
      outcome: 'error',
      durationMs: Date.now() - start,
      errorMessage: message,
    });
    throw err;
  }
}

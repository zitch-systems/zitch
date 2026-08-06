# WhatsApp production operations

The WhatsApp webhook is an authenticated ingress, not a job runner. In production it
stores each Meta message in Postgres, acknowledges only after the insert commits, and a
dedicated worker executes the command. Broadcasts use the same database-backed outbox.

## Safety properties

- Meta webhook bodies require `X-Hub-Signature-256` and the configured phone-number ID.
- `wa_message_id` is unique, so Meta redelivery cannot execute a command twice.
- Original command text is encrypted with `WHATSAPP_QUEUE_KEY` and erased after success
  or terminal dead-lettering. The support log retains only redacted text.
- A five-minute lease recovers jobs abandoned by a crashed worker. Retryable responses
  (currently HTTP 429) back off; an ambiguous timeout/5xx is recorded as `unknown` and
  is never blindly resent.
- Every broadcast requires a different operator to approve it. Recipients are
  materialised before the first provider call, and marketing recipients must have
  `marketing_opt_in=true`.
- A production worker refuses to start unless the channel is fully live. This prevents
  it executing a financial command while unable to send a receipt.

## Required Render topology

`render.yaml` defines:

1. `zitch-api` (paid web service),
2. `zitch-whatsapp-worker` (paid background worker),
3. `zitch-cache` (private shared Key Value), and
4. `zitch-db` with no public Postgres allow-list entries.

The worker references the API service's secret environment variables with Render
`fromService.envVarKey`; do not copy or independently rotate them. The API and worker
must share `DJANGO_SECRET_KEY`, `DJANGO_KYC_HASH_KEY`, `DJANGO_OTP_HASH_KEY`,
`WHATSAPP_QUEUE_KEY`, `DATABASE_URL`, and `REDIS_URL`.

To rotate the queue key without losing an in-flight command, first copy the old value
to `WHATSAPP_QUEUE_KEY_PREV`, set a new 32-byte-or-longer `WHATSAPP_QUEUE_KEY`, and deploy
both API and worker. After the queue contains no jobs encrypted before that deployment,
remove `_PREV`. New payloads are always encrypted with the current key.

The connected Render workspace was inspected on 2026-08-01. At that point there was no
Key Value, worker, or declared cron service; the web service was on the free plan with a
stale start command, and Postgres allowed `0.0.0.0/0`. Repository changes do not alter
those live resources.
An operator must review the paid resources and either sync the Blueprint against the
existing service or apply the equivalent dashboard changes. Confirm that Render links
the existing display-named service (`zitch main app`, slug `zitch-api`) rather than
creating a duplicate.

## Activation sequence

1. Merge only after CI is green and apply migrations before starting the worker.
2. Provision/link `zitch-cache`; confirm both processes receive the same `REDIS_URL`.
3. Configure identical WhatsApp/provider secrets on `zitch-api`; worker references them.
4. Keep `WHATSAPP_MODE=disabled` until Meta verification, templates, callback URL and
   (if used) Flow public key are complete.
5. Set `WHATSAPP_MODE=live` and start the worker. A missing live credential causes a
   fail-fast boot error.
6. Send one signed test message, then run an approved one-recipient utility template.
   Confirm inbound `processed_at`, recipient status, and audit entries.
7. Monitor queue age, `processing_error`, broadcast `unknown`, worker restarts, Meta
   delivery failures, and Sentry. Treat `unknown` as a manual reconciliation case.

## When the bot stops replying

This failure is quiet by construction. The webhook's job is to store and acknowledge,
so it answers Meta `200` whether or not anything downstream is working; the router —
where every log line and audit row is written — is only reached by the worker. A dead
worker therefore produces a chat that goes silent and a service that looks perfectly
healthy from `/healthz`, the Render metrics, and Meta's delivery reports alike.

Start at **`/admin/diagnostics/` → WhatsApp**, which needs nothing but an admin login,
and read `verdict` first. (`GET /whatsapp-diagnose` returns the same JSON for
scripting.) The causes it distinguishes, in the order they actually occur:

| What you see | What it means | Fix |
| --- | --- | --- |
| `worker_appears_stalled: true`, `unprocessed` climbing | Nobody is draining the queue — usually `zitch-whatsapp-worker` is not running, was never created, or crashed at boot | Start/redeploy the worker in Render. To unblock the waiting customers immediately, select their rows in **WhatsApp → Message logs** (filter `processed_at` = empty) and run **Process now** — it runs the same job in the web process |
| `mode: disabled` | No token / phone-number id, so the webhook returns `404` and Meta gets nothing | Set the WhatsApp credentials and `WHATSAPP_MODE=live` |
| `mode: sandbox` | Inbound is routed, outbound is mocked — replies are generated and thrown away | Same as above |
| `dead_lettered` above zero | The worker ran and kept failing | Open those rows and read `processing_error` |
| `handed_to_human` lists a number | Deliberate: `status=human` mutes the bot so an agent can take over, and it stays muted until someone closes it | **WhatsApp → Conversations** → **Return to bot** |

The worker refuses to boot unless the channel is fully live, so a half-configured
deploy shows up as a stalled queue rather than as replies sent without receipts.

## Commands

```bash
# One safe local/test drain
python manage.py whatsapp_worker --once

# Production worker command (already in render.yaml)
python manage.py whatsapp_worker --batch-size 50 --poll-seconds 0.5

# Schema and regression gates
python manage.py makemigrations --check --dry-run
python manage.py test whatsapp admin_api.test_operator_controls admin_api.tests
```

Do not run more workers as a throughput shortcut without load-testing row-lock
contention and Meta rate limits. Database claims prevent duplicate ownership, but Meta
quota and downstream financial-provider capacity remain external constraints.

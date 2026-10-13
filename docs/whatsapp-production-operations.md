# WhatsApp production operations

Current operating route: **Wema Partnership**. See [the current release review](partnership-audit-2026-10-13.md) and [Partnership return](wema-partnership-return.md) before changing provider configuration. VAS enrollment is closed; historical VAS notifications and reconciliation remain enabled.

## Runtime and delivery

The API verifies Meta signatures and the configured phone-number ID, stores accepted messages in Postgres, and acknowledges after the insert commits. The dedicated WhatsApp worker processes the durable queue. Duplicate message IDs cannot execute a new command twice.

The API, worker and scheduled jobs run in Frankfurt. Verify the actual service IDs and deployed revisions in Render; repository configuration alone does not establish live state. Database external access remains closed. API and worker must share the appropriate database, cache, identity/OTP hashing and queue encryption configuration.

Sensitive queued content is encrypted; operational logs retain redacted text. Provider timeouts with an uncertain send outcome remain uncertain rather than being blindly resent. Review dead letters, queue backlog and outbound delivery errors through authenticated diagnostics.

Public `/healthz` and `/readyz` establish service health only. Use authenticated `/admin/diagnostics/`, the internal WhatsApp diagnostics and Meta delivery evidence to verify:

- signed incoming events reach the correct account and number;
- the dedicated worker is processing current jobs;
- outgoing messages are accepted and delivered;
- the configured published Flow contains the screens the backend opens;
- payment and verification callbacks reach the correct live endpoints.

A published Flow or a valid Meta token alone does not prove a complete customer journey. Meta can omit subscribed-field metadata; an omitted field list is unknown, not proof of an absent subscription.

## Signup and identity

New customers can start signup from WhatsApp. Private signup/password, identity and transaction-PIN inputs use the secure Flow or the implemented secure browser journey. Do not ask customers to paste passwords, PINs, BVNs or NINs into the conversation.

The account begins unverified. Required contact proofs are completed before identity authorization. The current tier contract is:

| Tier | Requirement |
| --- | --- |
| Tier 1 | BVN **or** NIN, using bank SMS OTP or the bank-hosted face route, with attributable account creation. |
| Tier 2 | Prembly interactive live face proof and the identity information required for the bank upgrade. Unavailable until the authenticated live-session adapter is validated. |
| Tier 3 | Address verification completed by the bank and tied to the submitted address and request. Unavailable until that correlation can be verified. |

An uncertain account-creation result remains under review. Do not delete the attempt or create another account blindly. An existing successful identity proof must not be replaced by another onboarding loop.

## Authorization and payment outcomes

PIN authorization and insertion into the execution queue occur atomically. Worker retries use the original durable action/reference and cannot create a second purchase. Queued unlocks require the same current linked channel and credential state; unlinking or changing credentials invalidates the queued unlock.

Opening-history holds, unresolved migration exposures, account suspension and reversal evidence remain effective on both channels. Show total funds, available funds and review status consistently. A bank or provider timeout is not proof of payment failure and must not trigger an assumed refund.

Payment receipts and Flow completion screens must reflect the canonical ledger and any active reversal review. A “Done” screen must not convert an unresolved transaction into success.

## Operational checks before release

Run `python manage.py wema_preflight --strict` for the full declared scope. `--launch-scope core` explicitly excludes higher tiers and unsupported products; it still requires current successful financial-control evidence. A GO result cannot certify real-device delivery or bank settlement acceptance.

Verify authorized live signup/OTP, Tier 1 verification, account lookup, incoming credit, outgoing transfer/status, bill purchase/refund, delayed and duplicate callbacks, missed-notification recovery and customer receipts. Verify the matching bank and ledger entries and the current backup restore procedure.

Do not send customer test messages or initiate real-money transactions without a specified authorized recipient and test transaction. Credentials stay in the provider/deployment secret stores, never in chat or source control.

# Zitch VAS launch readiness — 7 October 2026

The Step 4 package has been sent according to the operator, and Wema acknowledged
it in Slack. This is a bank-validation submission, not public-launch approval.
Preserve the submitted API origin, token and sample history while Wema tests.
Do not change the prefix or enable real-money enrollment until written approval.

## Confirmed before this release

- API and worker run in Frankfurt. Database access is private; no Oregon services
  appeared in the current Render inventory. The published encrypted WhatsApp Flow
  has no Meta validation errors and matches the configured approved Flow ID.
- Three named, consented 711 accounts were provisioned. Authenticated public lookup,
  KYC and statement calls succeeded for each. One sample was later blocked at
  13:06 UTC with “Fraud test”; two remain active. No VAS receipts existed at the
  latest audit. A block is not proof that Wema accepted the complete integration.
- Prembly and Termii credentials are configured, provider simulation and production
  test OTP are off, email credentials are present, and all alert channels are enabled.
  No WhatsApp messages were pending or overdue at the runtime audit.
- Partnership new-business flows are archived. The separate Wema bill service is
  retained. Collection-funded VAS bills remain disabled without bank approval.

## Changes in this preparation release

- App account setup accepts consent and one BVN/NIN entry, then a provider-record
  ownership OTP. Encrypted ten-minute state is bound to user, app session, device,
  mode and consent version. Confirm/resend are serialized and bounded; account
  allocation still enforces the existing named proof and migration controls.
  A verified allocation retry does not ask for BVN or OTP again while the private
  challenge remains valid. A fresh challenge can reuse matching durable named proof.
- App status and loading failures are explicit; funding and external-bank actions
  require a real available live account. Test accounts do not supply deposit details.
- WhatsApp keeps stable menu numbers but marks unsupported outgoing transfers and
  QR payments unavailable instead of inviting a transfer that cannot be executed.
- Payment alerts distinguish total wallet funds from the amount available for VAS
  bills and historical funds. They never label retained historical balances as
  available VAS spending money.

## External and operator requirements

| Requirement | Evidence needed before enabling the capability |
| --- | --- |
| Wema account activation | Written five-endpoint acceptance, assigned live prefix, approved collection/settlement account and live approval reference |
| Real inbound collections | Bank-led credit, notification/replay tests, collection statement/balance evidence, fees and settlement timeline; match customer liability to bank-held assets |
| Missing-notification recovery | Production Transaction Search URL, authentication, pagination, retry semantics and bank re-push/escalation procedure; current snapshot reader is offline only |
| Outgoing transfers | Complete payout initiation, idempotency, Outward TSQ and reversal contract plus entitlement; current VAS code has no live outward adapter |
| Bill payments using VAS funds | Written permission for the collection account to fund retained bill APIs, approved source, status/requery entitlement and statement debit/refund mapping |
| Existing users | Bank migration instructions and immutable per-user approval, cleared pending work and reconciled old liabilities; the approved 711 retained-balance exception does not extend to live money |
| Notifications | Monitored service-update mailbox; approved transaction utility template; Android Firebase client/FCM v1 and iOS APNs setup; live device/email/SMS delivery evidence |
| Mobile distribution | New Android build containing this patch, actual upload signing and physical-device acceptance; iOS signing, build and TestFlight/App Store release remain unverified |
| Recovery | Restore a current Frankfurt backup into an isolated database and validate it; downloaded archival bytes alone do not prove recovery |

Wema's [24 September response](https://wemabankteam.slack.com/archives/C0BDAQF7U56/p1790250918782509)
said the outbound/Outward TSQ documentation would follow endpoint submission.
Receiving callback approval or credentials does not implement that missing rail.
Do not invent endpoint paths, status semantics or reversal behaviour.

## Unresolved evidence from this audit

- Latest stored bank reconciliation reports aggregate legacy ledger excess of
  **NGN 111.25** over bank available balances across three profiles. The difference
  could involve charges, held funds or unmatched history; it has not been
  attributed. Preserve balances and ledger records while obtaining bank evidence.
- One historical customer still has unknown Partnership issuance state. Do not
  automatically clear that state or allocate around an unresolved bank outcome.
- The missing `txn_alert` utility template was submitted to Meta as
  `1083297937653992` and returned `PENDING`. Submission is not approval or delivery.
  Its two body variables match the existing application configuration. No customer
  message was sent. Check current approval status before launch.
- The connected Resend account lists a different project's domain. It cannot prove
  Zitch's sender status; the deployed Zitch key must be checked separately without
  replacing it with another project's key. Inbox monitoring is still unconfirmed.
- Android Firebase native client configuration is absent from app config/build
  workflows. A notification permission prompt alone does not register a working
  push token. Obtain the matching Firebase client and FCM delivery configuration;
  verify native device delivery. iOS APNs and signed distribution are also unverified.
- The Oregon retirement archive passed full tar/gzip member integrity checks,
  but local PostgreSQL restore tooling is unavailable and package installation is
  blocked by the runtime. This archive is not a current Frankfurt restore drill.

## Activation order

1. Collect the bank's signed-off capabilities and production values above. Record
   actual approval references; never use placeholders or treat an env var as proof.
2. Configure live mode, assigned non-711 prefix, collection account and approval
   consistently across all nine Django runtimes with enrollment still closed.
   Retain identity keyring, callback token, immutable samples and old financial history.
3. Admit only bank-approved pilot profiles. Customers give the separate live consent;
   allocate a separate live account rather than changing any 711 number or mode.
4. Verify real bank inflow and settlement, duplicate notification handling, restriction
   handling, and agreed recovery. Exercise app and WhatsApp against the same account.
   Money movement requires the account holder/operator's authorized test action.
5. Enable bills only after collection debit approval and live acceptance. Enable
   outgoing transfers only after their adapter and bank acceptance exist.
6. Complete the signed mobile build/device checks and recovery evidence, record
   general-release approval, then open general enrollment. To pause, close customer
   enrollment; keep bank callbacks available for funds already sent.

The latest CI/deployment/artifact evidence belongs in the operational handover.
This checklist deliberately does not claim that credentials alone permit immediate
full banking launch.

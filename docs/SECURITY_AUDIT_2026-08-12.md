# Zitch end-to-end security audit — 2026-08-12

Scope: Django API and operator surfaces, WhatsApp Cloud API/Flows, wallet and
settlement state machines, Wema/ALAT and other provider adapters, PostgreSQL and
Redis usage, Render deployment, Expo mobile application, dependencies and CI.

This is an engineering assurance report, not a guarantee that the system is
"100% secure". No defensible security review can promise immunity from every
future vulnerability or AI-assisted attack. The release decision below is based
on controls and evidence that can be tested.

## Release decision

| Target | Decision | Reason |
|---|---|---|
| Controlled fake-money E2E | **GO after this PR deploys** | BVN/NIN can be entered and OTP-confirmed in explicit simulation; Termii SMS and Resend email remain real. Simulation and fixed-test controls are visible go-live blockers. |
| Public beta with real money | **NO-GO** | Live identity/provider contracts, paid queue worker, database isolation/availability, enforced operator MFA, production callbacks and signed-device live-rail tests are not yet evidenced. |

Do not disable `WEMA_SIMULATION`, change Wema/Prembly hosts, or enable customer
real-money access merely because the code and unit tests pass. Provider-side
activation and independent settlement evidence are required.

## Critical and high-risk findings remediated in this change

| Area | Finding | Remediation |
|---|---|---|
| KYC | Selfie, address, NIN-document and ID-document checks silently mock-passed when Prembly was absent in production. | All such checks fail closed outside development or the explicit production-simulation mode. |
| BVN/NIN ownership | Mobile BVN OTP went to the Zitch account phone/email instead of the identity-provider record; NIN could become verified without an ownership challenge. | Live OTP goes only to the provider-record phone. Explicit simulation uses the tester's account phone plus email and requires real Termii/Resend delivery. Both identities share a six-digit, HMAC-stored, five-attempt challenge; the pending identity is Fernet-encrypted in shared cache and expires after ten minutes. |
| Identity persistence | A raw pending BVN/NIN would otherwise be needed between start and confirmation. | Raw identity is never stored on the user or in plaintext cache; only encrypted short-lived state, keyed persistent hashes and last four digits remain. |
| PIN policy | Mobile created/reset four-digit transaction PINs while the backend and WhatsApp expected six digits; the client warned against obvious PINs but direct API/Flow callers could bypass that warning. | All creation, reset, keypad and biometric-cache paths now require six digits; legacy current PINs can only authorize migration to a six-digit replacement. API, WhatsApp signup and WhatsApp reset centrally reject repeated or sequential choices before hashing them. |
| Provider settlement | Concurrent callback/request failure paths could double-refund or refund a payout already marked successful. | Transaction rows are locked as the state-machine authority. Only `PENDING -> FAILED` refunds; terminal rows are idempotent. Synchronous payout responses use the same locked settle/refund function as callbacks/reconciliation. |
| Mono funding | A “payment received” callback with no settled amount credited the customer's requested amount; malformed event/data shapes could reach callback logic. | Missing/malformed/non-positive amounts leave the intent pending. Verified amounts are capped at the original intent. Webhook secret is checked before body parsing, including in deployed simulation, and event/data must be JSON objects. |
| Internal transfers | Zitch-to-Zitch transfers mutated wallets directly and could race past locked daily/velocity limits. | Spend limits are re-evaluated while both wallet rows are locked. |
| API auth | Legacy access tokens in JSON bodies could enter WAF/error telemetry; operator endpoints had a separate unbounded/content-type-agnostic parser. | Production accepts bearer headers only. Customer payloads are capped at 4 MiB; operator JSON at 64 KiB; JSON content type and object shape are enforced consistently. |
| Request abuse | WhatsApp Flow envelopes and some webhook paths lacked an explicit small body boundary. | WhatsApp webhook and encrypted Flow envelopes are limited to 1 MiB; Mono is limited to 1 MiB; Wema's existing authenticated body limit remains. |
| Risk step-up | Removing `X-Zitch-Device` bypassed the new-device face step-up. | HTTP requests without a device ID are unknown devices; genuine non-HTTP WhatsApp/cron callers remain distinguishable. |
| Cache privacy | Rate-limit/login keys exposed raw IP/email/phone values to Redis operators and backups. | Identifiers are now scoped keyed HMACs, preventing recovery and offline enumeration. |
| Mobile secret storage | A cached transaction PIN was device-only but not protected by an OS-authentication ACL; legacy items could be silently reused. | PIN keychain access now requires fresh OS authentication. Unmarked legacy items are deleted and must be re-enrolled; PIN storage is disabled on web. |
| Web bearer storage | The Expo web preview persisted the customer bearer token in browser storage. | Web sessions are now memory-only and erase any legacy stored token; native sessions remain device-only keychain items. |
| Mobile capture | Balances, identity screens and PIN entry could be captured app-wide. | Native screen capture/recording is blocked globally. Explicit receipt export remains available. |
| Edge bypass | On a WAF 403 the app retried the same request against the Render-assigned origin, bypassing the edge policy and risking duplicate money POSTs. | Release builds use only `https://api.zitch.ng`; WAF refusals are never rerouted. |
| LLM SSRF | A configurable custom model endpoint remained an avoidable DNS-rebinding/SSRF surface. | Custom endpoints are disabled by default in production; the portal rejects/filters them. The model still cannot execute money movement. |
| Operator attack surface | Stock Django admin is password-only; MFA endpoints themselves lacked a narrow rate limit. | Public deployments now leave `/admin/` unmounted by default. Normal operations use the RBAC/TOTP portal. MFA enrol/confirm/disable are rate-limited. `OPS_REQUIRE_MFA` must be enabled after enrolment. |
| Test safety | A developer/CI environment containing real provider keys could let fixture data trigger live HTTP. | The Django test runner blocks unmocked `requests` network calls. Provider-contract tests must explicitly mock their transport. |
| Supply chain | CI scanned shallow Git history, skipped transitive Python dependencies and failed open when npm advisories were unavailable. | Full-history secret scan, transitive `pip-audit`, high/critical npm gating and fail-closed advisory outages are configured. Two unpatched `image-size` build-parser advisories have a narrow, expiring mitigation: vulnerable formats are disabled in Metro and absent from the repository. |

## Live Render evidence and configuration drift

Observed in workspace `tea-d8entvernols73agg0rg` without reproducing any secret:

| Component | Observed | Required before public launch |
|---|---|---|
| Web service `zitch main app` | Free plan, one instance, bare gunicorn start, health path `/`, inline WhatsApp processing enabled | Paid non-sleeping service, reviewed gunicorn command, `/healthz`, checked deploys, web inline processing off after worker validation |
| WhatsApp worker | No worker service exists | Provision the paid `zitch-whatsapp-worker`, prove leases/retries/dead-letter and zero growing backlog |
| PostgreSQL | Basic 256 MiB / 1 GiB, no HA, no pool; live configuration does not evidence an empty public allowlist | Disable public access, use private connections, validate backups and restore, define HA/capacity/RPO/RTO |
| Redis/Key Value | Starter, persistence off; reachable by the app | Retain for ephemeral limits/challenges only, restrict external access, monitor evictions/availability; durable WhatsApp work remains in Postgres |
| Blueprint | Repository declares starter web/worker/cache, `/healthz`, checked deploys and `ipAllowList: []` | Review cost and sync deliberately; confirm actual resources match the Blueprint afterward |
| Provider state | Health evidence showed Wema simulation on, Wema live false, sandbox true, Prembly false; Termii and Resend keyed; WhatsApp live/Flow configured | This is suitable only for fake-money testing. Obtain and verify live provider contracts/credentials before changing it. |

The cloud browser could not reach either public API hostname because its network
policy returned `ERR_BLOCKED_BY_CLIENT`; that is not evidence that Zitch was down.
Live HTTP/Flow delivery must therefore be repeated from an ordinary device or an
approved external probe after deployment.

## Verification evidence

| Check | Result |
|---|---|
| Django full regression on final working tree | 1,388 tests passed |
| Focused security regression | 260 tests passed |
| Django system check | Passed |
| Django deploy check | Passed, with the intentional HSTS-preload policy check silenced |
| Migration drift | No changes detected |
| Mobile Jest | 10 suites / 87 tests / snapshot passed |
| Mobile lint and TypeScript | Passed |
| Expo Doctor and dependency compatibility | 20/20; dependencies compatible |
| Android production export | Passed, 1,950 modules bundled |
| Python dependency audit | No known vulnerabilities |
| Bandit medium/high | No findings after reviewed false-positive annotations |
| npm production audit | Passed policy; two mitigated, expiring `image-size` build-time advisories remain until upstream fixes |
| Diff whitespace / Python compile | Passed |

GitHub Actions must pass again on the committed revision before this draft PR is
eligible to merge.

## Hard launch gates still open

1. **Identity proofing:** obtain current Wema and Prembly production contracts,
   verify endpoint/field/status semantics against provider fixtures, and prove
   BVN and NIN ownership with real test identities. Wema alone does not provide
   the standalone lookups the current mobile flow expects.
2. **Real communications:** prove Resend sender-domain acceptance and Termii
   delivery (including DND routes and approved sender ID) to controlled Nigerian
   numbers. A configured key is not delivery evidence.
3. **Wema settlement:** rotate credentials, configure strong callback and
   `securityInfo` secrets, enforce confirmed bank source IPs, profile all callback
   URLs, and run success, timeout, duplicate, late-success, failure, reversal and
   reconciliation scenarios against the bank environment.
4. **Render topology:** approve the recurring cost, provision the worker/paid web
   plan, restrict PostgreSQL, verify backup restore and capacity, then prove queue
   and reconciliation behavior under process termination and concurrency.
5. **Operator security:** enrol at least two independent super-admin/finance TOTP
   factors, store recovery procedures offline, enable `OPS_REQUIRE_MFA=true`, keep
   `DJANGO_ADMIN_ENABLED=false`, and exercise maker/checker controls.
6. **Mobile release assurance:** test signed Android and iOS builds on physical
   devices, including secure storage invalidation, rooted/jailbroken-device risk,
   screen capture, deep links, offline retry/idempotency and live WhatsApp handoff.
   Certificate pinning is deliberately not enabled without owned primary and
   backup keys plus a rotation plan; decide and document the TLS/WAF trust model.
   Replace reusable biometric-cached PIN storage with a device-bound signing key
   and server challenge before treating biometrics as a high-assurance payment
   authorization factor.
7. **Operational assurance:** configure retained redacted logs/Sentry alerts,
   fraud and reconciliation paging, incident response, key rotation, penetration
   testing, DAST/load/abuse tests from an allowlisted environment, and an
   independent review of licensing, CBN/NDPR/PCI obligations. Add a reproducible,
   hash-pinned Python dependency lock/SBOM. Define retention and application-layer
   encryption/KMS treatment for operator TOTP seeds and sensitive transaction
   metadata, and validate server-side customer session expiry/revocation policy
   rather than relying only on the client's idle lock.

## Adversarial testing boundary

Testing in this audit was bounded to the owned codebase, local test databases,
dependency/static analysis and read-only deployment inspection. It did not run
credential stuffing, destructive load, unrestricted brute force, malware,
phishing, denial of service, or attacks against Meta/Wema/Termii/Resend/Render.
Those actions require explicit written scope, rate limits, test accounts,
provider approval and an agreed rollback/incident window.

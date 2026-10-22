# Prembly Tier 2 SDK

Tier 1 accepts a verified BVN or NIN. Tier 2 adds an authenticated, completed
interactive face session; a BVN-verified customer does not need NIN verification.
This changes the local verification ladder. Wema's account tier and funding or
spending limits remain independently enforced.

The configured live SDK workflow uses BVN, face scan, gesture liveness and face
comparison. Its BVN input must match the identity hash already stored by Zitch.
The initial workflow supports BVN customers; NIN-only customers retain their
verification and receive an explicit unsupported-workflow message.

## Configuration

Set these variables on the API and WhatsApp worker using Render secrets:

- `PREMBLY_API_KEY`: existing secret key for authenticated session readback.
- `PREMBLY_FACE_WIDGET_ID`: the live workflow's Config ID.
- `PREMBLY_FACE_WIDGET_KEY`: its public SDK widget key.
- `PREMBLY_FACE_SDK_ENABLED=true` after configuring mandatory face scan, gesture
  liveness and face comparison. The workflow must use the BVN channel.

The Blueprint carries placeholders on the API and `fromService` references on
the worker. Direct Render changes must carry the same values on both services
until the next Blueprint sync. Never commit credential values.

The workflow return URL is `https://api.zitch.ng/kyc/face/complete/`. That page is
informational and cannot grant verification. Customers can check completion in
the app, on the PIN-gated WhatsApp page, or through WhatsApp option 8.

## Authorization

The server creates the provider session, saves its unique ID with the user,
identity hash, credential fingerprint, action nonce and 20-minute expiry, then
opens the provider URL. It never accepts a caller-selected provider session ID.

Completion requires a secret-authenticated session readback with the same
session, widget and action, a completed passing result, a completed live gesture
attestation, a successful face comparison, and the same BVN. Identity and
credentials are checked again under a user lock before committing proof.
Changing contact verification, identity or credentials invalidates the grant.
Web PIN authorization is checked again after provider I/O. Provider images and
raw identity numbers are not saved locally. Unknown result shapes remain pending.

The older still-image bank upgrade stays unavailable: a session result does not
certify a client-supplied bank image or change `Wallet.bank_tier`.

## Validation and remaining customer action

Live initiation and secret-authenticated pending-session readback were exercised
against the configured workflow on 22 October 2026. Readback confirmed face scan,
gesture liveness and face comparison enabled. No synthetic face was submitted.
The successful biometric branch is covered by contract tests; an actual customer
must complete the camera challenge to validate a live successful result. If a
provider result uses an unrecognized shape, inspect only redacted structure and
adapt the parser to authenticated evidence. Do not substitute a browser callback
or remove the identity/liveness requirements.

Account issuance is separate. Use the audited `prepare_account_recovery` command
only after its provider lookup confirms no bank account exists; preserve verified
identity flags. Recovery exposes `recovery_ready` and continues through the bank's
secure face step. It must never pretend that an unconfirmed account is processing.

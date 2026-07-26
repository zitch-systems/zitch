# Maestro device flows

[Maestro](https://maestro.mobile.dev) UI flows for the Zitch APK. Use them to
smoke-test a build on a real Android device or emulator, and to walk the full
fake-money onboarding + funding path against a **simulation** backend.

## Flows

| File | What it does | Needs a backend? |
| --- | --- | --- |
| `smoke.yaml` | Launch → Skip → sign-in screen renders and validates empty input. | No |
| `onboarding.yaml` | Carousel → redesigned register screen; asserts the 4-step progress stepper and the required **Full name** field. UI-only (no submit). | No |
| `signup_fund.yaml` | Full E2E: sign-up → OTP → password → PIN → dedicated funding account (Wema sim) → simulated deposit → balance. | **Yes — simulation mode** |

## Prerequisites

- Install Maestro: `curl -Ls https://get.maestro.mobile.dev | bash`
- Install the debug-signed APK from the Codemagic `android-apk` build on a
  connected device/emulator (`adb install app-release.apk`).

## Running

```bash
# No-backend UI checks
maestro test .maestro/smoke.yaml
maestro test .maestro/onboarding.yaml

# Full fake-money E2E (secrets passed at runtime — never commit them)
maestro test .maestro/signup_fund.yaml \
  -e API_URL=https://api.zitch.ng \
  -e TEST_PHONE=08160000001 \
  -e TEST_OTP_CODE=123456 \
  -e SIMULATE_DEPOSIT_TOKEN=<server secret> \
  -e DEPOSIT_AMOUNT=50000
```

## Backend simulation setup (`signup_fund.yaml`)

The APK targets the host in `codemagic.yaml → EXPO_PUBLIC_API_URL`
(`https://api.zitch.ng`). That backend must have these **temporary** env vars set
(see `docs/CODEMAGIC_APK_E2E_TEST.md`):

- `WEMA_SIMULATION=true`
- `TEST_OTP_PHONE=<TEST_PHONE>` and `TEST_OTP_CODE=<fixed code>`
- `SIMULATE_DEPOSIT_TOKEN=<long random secret>`

Pre-warm and verify before running: `GET /healthz` should report
`funding_wema_simulation: true`, and `GET /readyz` should report `db: true`.

> **Only enable these on a backend with no real users.** After testing: remove
> `TEST_OTP_*` and `SIMULATE_DEPOSIT_TOKEN`, set `WEMA_SIMULATION=false`, and run
> `python manage.py wema_preflight` before any live-money use.

## Notes & caveats

- **One-shot per phone.** The API refuses a sign-up OTP into an account that
  already has a password, so `signup_fund.yaml`'s sign-up leg only works on a
  phone with no account yet. Reset (delete) the `TEST_PHONE` user between runs.
- **Two OTPs, two sources.** `TEST_OTP_CODE` is the phone sign-up OTP. The
  dedicated-account (Wema) provisioning OTP is a *separate*, provider-mocked code
  (`WEMA_SIM_OTP`, default `123456`) — any value is accepted in simulation.
- **OTP entry** relies on the verify screen auto-focusing its hidden input. If a
  device/keyboard steals focus, add a `tapOn` on the code boxes before
  `inputText`.
- These flows are authored against the current screen text; they have not been
  executed in CI. Validate on-device and tweak selectors if the copy changes.

## Extending: add a P2P transfer leg

After the deposit lands, a Zitch-to-Zitch transfer can be appended to
`signup_fund.yaml`. It needs a **second** registered test account
(`TEST_PHONE_2`) to receive funds. Sketch (adjust selectors to `sendmoney.tsx`):

```yaml
- tapOn: "Send"            # or the Send-money quick action on Home
- tapOn: "Recipient"       # enter TEST_PHONE_2, wait for name resolution
- inputText: "${TEST_PHONE_2}"
- tapOn: "Amount"
- inputText: "1000"
- tapOn: "Continue"
- tapOn: "1"               # authorize with the 4-digit PIN (1357)
- tapOn: "3"
- tapOn: "5"
- tapOn: "7"
- assertVisible: "Successful"
```

Then assert the debit appears exactly once in transaction history and that a
retried request does not double-charge (the core money-safety check from the
E2E runbook).

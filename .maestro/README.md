# Maestro device flows

The 25 July audit's P1 item: mobile coverage was 20 unit tests plus a browser export
inspection, with no flow ever exercised on a real handset. These flows cover the
journeys where a failure costs money or locks a customer out.

## Honest status

**These flows have not been run on a device or emulator.** They were written against
verified selectors — every `assertVisible` / `tapOn` string below was read out of the
screen's source, not guessed — but selector-correct is not the same as passing. The
first run will need adjustment for timing (`extendedWaitUntil` values), scroll
positions, and any screen that renders its label differently at runtime.

Treat the first execution as part of the work, not a formality.

## Running them

```bash
# once
curl -fsSL https://get.maestro.mobile.dev | bash

# a device or emulator must be attached (adb devices / xcrun simctl list)
npm run test:e2e                       # every flow
npm run test:e2e -- signin-biometric-fallback   # one flow by name
```

Flows that need a signed-in session read credentials from the environment, so no test
account is committed:

```bash
export ZITCH_TEST_PHONE=08012345678
export ZITCH_TEST_PASSWORD='…'
export ZITCH_TEST_PIN=1234
npm run test:e2e
```

`scripts/maestro.sh` fails with a clear message when a flow needs a variable that is
not set, rather than running and failing on an empty text field.

### Point them at a test backend

The flows drive the app against whatever `EXPO_PUBLIC_API_URL` the build was made
with. Do **not** run the money flows against production: `transfer.yaml` and
`utility-airtime.yaml` stop at the PIN sheet by design (see below), but a build
pointed at production is one tap away from a real debit.

Build against a deploy with `WEMA_SIMULATION=true`, and use the documented test-OTP
bypass (`TEST_OTP_PHONE` / `TEST_OTP_CODE` / `ALLOW_PRODUCTION_TEST_OTP`) so
`signup-otp.yaml` can complete. See `docs/wema-go-live-runbook.md`.

## What each flow covers

| Flow | Journey | Why it matters |
|---|---|---|
| `onboarding.yaml` | the three intro screens into registration | the first screen a new user sees |
| `signup-otp.yaml` | registration → OTP → PIN setup step labels | the funnel where a broken step means zero signups |
| `signin-biometric-fallback.yaml` | biometric affordance present, password path still works | a biometric prompt that cannot be dismissed locks every user out |
| `smoke.yaml` | sign-in validation errors | cheapest possible "is the app alive" check |
| `add-money.yaml` | funding account visible and copyable | money cannot come IN without this screen |
| `transfer.yaml` | recipient → amount → PIN challenge appears | stops at the PIN sheet: asserting the challenge appears is the security property, and typing the PIN would move real money |
| `utility-airtime.yaml` | network → amount → PIN challenge appears | same boundary, same reason |
| `receipts.yaml` | history → transaction detail → share receipt | a receipt that will not open is a support call per transaction |
| `offline-retry.yaml` | airplane mode mid-purchase shows the honest message | the case that produces double-debits when handled badly |
| `session-lock.yaml` | backgrounding past the lock window returns to sign-in | an unlocked session on a lost phone is the whole threat model |

### Why the money flows stop at the PIN sheet

`transfer.yaml` and `utility-airtime.yaml` assert that **"Enter your PIN"** appears
and then stop. That assertion *is* the security property worth automating — every
money path must challenge for the PIN, and a regression that skipped it would be
critical. Completing the payment would move real value on any non-simulated backend,
and a suite that spends money on every run gets disabled.

To exercise settlement end-to-end, use the simulation walk in
`docs/wema-go-live-runbook.md` (`simulate-kyc` + `simulate-deposit`), which is
designed for exactly that and touches no real rail.

## Device coverage

The audit asked for Android 8/11/14/16-class devices. Maestro does not choose devices
— whatever is attached is what runs. Running this suite across that matrix is a CI or
device-farm configuration, and it is **not** done: nothing here provisions devices.
That gap is real and is separate from the flows themselves.

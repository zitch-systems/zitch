# Codemagic APK testing

The `android-apk` workflow builds an installable Android preview APK from the repository root. Codemagic is the build runner; Expo remains the native-project generator through `expo prebuild`.

## Before the first build

1. Connect `zitch-systems/zitch` in Codemagic and select `codemagic.yaml`.
2. Confirm the workflow environment points `EXPO_PUBLIC_API_URL` at the intended test backend. This value is compiled into the app and must never contain a secret.
3. Keep `TEST_OTP_PHONE`, `TEST_OTP_CODE`, and `SIMULATE_DEPOSIT_TOKEN` unset on a live backend. A deliberately isolated simulation backend must also set `ALLOW_PRODUCTION_SIMULATION=true`.
4. Set `REDIS_URL` on any multi-worker backend so rate limits and idempotency state are shared.

Start **Zitch Android APK (preview)** from the Codemagic dashboard. The workflow installs the locked dependencies, lints, type-checks, runs unit tests, blocks critical production dependency advisories, runs Expo Doctor, generates Android with Expo Prebuild, and assembles the release APK.

## Install and smoke-test the APK

Download the APK from the Codemagic artifact page and install it on a test device or emulator. Do not distribute this preview as a Play Store release: the generated preview release uses a development signing key.

With Maestro installed and the APK running on an Android emulator or connected device:

```sh
maestro test .maestro/smoke.yaml
maestro test .maestro/onboarding.yaml
```

The smoke flow verifies cold launch, onboarding-to-sign-in navigation, required fields, and empty-form validation. The onboarding flow verifies all three slides and registration navigation.

## Financial-flow test checklist

Use dedicated test users and non-production provider credentials for these checks:

- OTP registration, password and transaction-PIN setup
- biometric sign-in and biometric payment approval, including PIN fallback
- Wema account provisioning and an inbound deposit
- internal transfer, bank payout, and failed-payout reversal
- airtime, data, electricity, cable, betting, exams, savings, loan and card flows
- session lock, hard expiry, sign-out and account recovery
- network loss, duplicate taps, provider timeout and app restart during a pending transaction

Never use real customer credentials or production money to exercise failure paths. Verify the backend ledger and provider transaction after every money-moving test; a successful screen alone is not sufficient evidence.

## Play Store builds

A production AAB needs a persistent upload keystore configured in Codemagic and a separate signed workflow. Do not reuse the preview key. Store the keystore and passwords in Codemagic encrypted variables or its Android signing integration, then add `bundleRelease` only after the signing reference is known.

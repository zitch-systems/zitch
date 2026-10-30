# Codemagic APK end-to-end test

This runbook is for the installable preview APK produced by the root
`codemagic.yaml` workflow named **android-apk**.

## 1. Build the APK

1. Open the Zitch app in Codemagic.
2. Choose **Start new build**.
3. Select branch `main` and workflow **android-apk**.
4. Start the build.
5. Do not install the artifact unless all of these steps are green:
   - Install dependencies
   - Validate TypeScript
   - Run app unit tests
   - Validate Expo project (SDK-compatible dependencies and resolved config)
   - Expo prebuild
   - Build release APK
   - Verify APK artifact
6. Download the APK from **Artifacts** and install it on an Android test device.

The preview APK is sideloadable and debug-key signed. It is not a Play Store
release. Do not distribute it as a production binary.

## 2. Prepare a simulation backend

The APK currently targets `https://api.zitch.ng`. Complete fake-money testing is
possible only when the backend used by the APK has all of these temporary
environment variables:

- `WEMA_SIMULATION=true`
- `TEST_OTP_PHONE=<the phone used on the test account>`
- `TEST_OTP_CODE=<a temporary fixed OTP>`
- `SIMULATE_DEPOSIT_TOKEN=<a long random temporary secret>`

Never place these values in `codemagic.yaml`, `EXPO_PUBLIC_*`, the APK, or the
repository. The simulated-deposit token is a server secret.

If `api.zitch.ng` is serving real users, do not enable test switches there.
Deploy a separate staging/simulation backend and change
`EXPO_PUBLIC_API_URL` in the Codemagic workflow to that HTTPS host before
building the test APK.

## 3. Pre-warm and verify the backend

Render's free web tier can sleep between requests. Before opening the APK, verify
both process and database readiness from a trusted terminal:

```bash
curl --fail --show-error "https://<simulation-api>/healthz"
curl --fail --show-error "https://<simulation-api>/readyz"
```

Continue only when `/healthz` reports `"status": true`,
`funding_wema_simulation` is `true`, and `/readyz` reports
`{"status": true, "db": true}`. If the first request shows a Render wake-up page,
wait for the instance to start and repeat both checks.

## 4. Walk the app end to end

1. Install the new APK (remove an older build first if Android reports a signing
   conflict).
2. Register with `TEST_OTP_PHONE`.
3. Enter `TEST_OTP_CODE` at OTP verification.
4. Finish password, transaction PIN, and KYC/onboarding screens.
5. Credit the test wallet from a trusted terminal:

   ```bash
   curl -X POST "https://<simulation-api>/api/dev/simulate-deposit/" \
     -H "Content-Type: application/json" \
     -d '{"token":"<SIMULATE_DEPOSIT_TOKEN>","phone":"<TEST_OTP_PHONE>","amount":50000}'
   ```

6. Refresh the wallet and confirm the simulated credit appears.
7. Test a Zitch-to-Zitch transfer to a second test account.
8. Test airtime, data, cable, electricity, betting, exam PIN, savings, cards,
   loans, profile, session lock/unlock, logout, and sign-in again as applicable.
9. Confirm every successful debit appears once in transaction history and that a
   retried request does not double-charge.

## 5. Record failures

For each failure capture:

- app version from `app.json`
- Android model and OS version
- Codemagic build number and commit SHA
- screen/flow, input (redact PINs, OTPs, tokens, BVN/NIN, and card data)
- expected vs actual result
- time of failure, so backend logs can be correlated

## 6. Mandatory cleanup

Immediately after the simulation pass:

- remove `TEST_OTP_PHONE`
- remove `TEST_OTP_CODE`
- remove `SIMULATE_DEPOSIT_TOKEN`
- set `WEMA_SIMULATION=false` before any live-money testing
- run `python manage.py wema_preflight`; production readiness must pass
- uninstall the debug-signed APK from test devices when testing is complete

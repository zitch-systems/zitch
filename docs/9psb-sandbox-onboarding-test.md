# 9PSB sandbox onboarding test: signup, KYC and a test NUBAN

**Scope:** one enrolled tester signs up, verifies phone and email, verifies a
bank-issued sandbox BVN or NIN with the 9PSB OTP, and gets a 9PSB NUBAN.
Funding, transfers and VAS are out of scope here. Passing this test is
connectivity and flow evidence, not bank UAT or production approval.

## 1. Before you start

| Requirement | Why |
|---|---|
| PR #561 and its follow-ups merged and deployed to the API **and** worker | Production still serves `main` without the `/api/banking/` routes. Without them the app has no 9PSB onboarding. |
| An Android test APK built from that merged `main` | The KYC screen calls `/api/banking/identity/*`. Older APKs call retired routes. |
| Bank-issued sandbox identities (see section 3) | The sandbox is plain HTTP. Never enter a real BVN, NIN, face photo or address. |

## 2. Server configuration (Render API service; worker and maintenance inherit)

| Variable | Value for this test |
|---|---|
| `NINEPSB_ENVIRONMENT` | `sandbox` |
| `NINEPSB_BASE_URL` | `http://102.216.128.75:9090` (WaaS origin; no path) |
| `NINEPSB_WAAS_USERNAME`, `_PASSWORD`, `_CLIENT_ID`, `_CLIENT_SECRET` | WaaS section of the bank's test credentials |
| `NINEPSB_ENABLED` | `true` |
| `NINEPSB_SANDBOX_CUSTOMERS_ENABLED` | `true` |
| `NINEPSB_SANDBOX_PHONES` | Comma-separated tester phones, e.g. `08012345678` (any of `0…`, `234…`, `+234…`) |
| `NINEPSB_SANDBOX_USER_IDS` | Alternative to phones once a tester's user ID is known |
| `NINEPSB_UAT_APPROVED` | `false` |
| `NINEPSB_VAS_ENABLED` | `false` until VAS authentication succeeds (section 7) |
| `BANKING_ENCRYPTION_KEYS` | A dedicated key, shared by API, worker and maintenance. If unset it falls back to `DJANGO_SECRET_KEY`. |
| Phone OTP | `TERMII_API_KEY` with an approved DND sender ID, **or** for one tester `TEST_OTP_PHONE` + `TEST_OTP_CODE` + `ALLOW_PRODUCTION_TEST_OTP=true` (remove before launch) |
| Email OTP | `RESEND_API_KEY` and a verified `RESEND_FROM_EMAIL` domain |

Values are stripped of surrounding whitespace, so a pasted trailing newline no
longer disables a flag or corrupts a credential.

Then, from a Render shell on the API service:

```sh
python manage.py bank_preflight --report-only          # config only, no bank calls
python manage.py bank_sandbox_check --product waas      # WaaS login + bank directory
```

`bank_preflight` always lists `sandbox_only` and the UAT/production blockers;
those are expected for this test. `bank_sandbox_check` must report
`waas_authentication` and `bank_directory` as `passed`.

## 3. Ask 9PSB for these before testing

Account opening is still untested against the bank. These are the gaps that
usually stop a first run:

1. **Sandbox test identities.** One BVN (or NIN plus its NIN user ID) per tester,
   with the registered first name, last name, date of birth, gender and phone.
2. **How the sandbox identity OTP is delivered.** Is it a fixed code, or is it sent
   to the phone on the BVN? Zitch submits the tester's verified Zitch phone as
   `phoneNo`.
3. **Which name `open_wallet` returns.** Is it the submitted `otherNames`/`lastName`
   or the BVN record? Zitch refuses to attach a NUBAN whose name differs from the
   Zitch profile.
4. **Whether a test identity can open more than one wallet.** Zitch binds each
   BVN/NIN to one Zitch user, so each tester needs a distinct identity.

## 4. Check the tester before they open KYC

```sh
python manage.py bank_tester_status --phone 08012345678
```

It makes no bank request and prints only the user ID and last four digits. It
names every failing gate in order, with `next_step` for the first. Common
blockers:

| Blocker | Fix |
|---|---|
| `banking_disabled`, `sandbox_customers_disabled`, `waas_not_configured` | Section 2 variables |
| `not_enrolled` | Add the phone to `NINEPSB_SANDBOX_PHONES` or the ID to `NINEPSB_SANDBOX_USER_IDS` |
| `enrolled_phone_not_verified`, `phone_not_verified` | Finish phone OTP in the app |
| `email_not_verified` | Confirm the email code in the app |
| `legal_name_incomplete` | The profile needs a first **and** last name |

Proceed when it reports `"ready_for_bank_identity": true`.

## 5. Test steps in the app

1. **Sign up** with the enrolled phone. Use the **same first and last name as the
   bank test identity**. Verify the phone OTP, then set a password and PIN.
2. **Verify your email** on the screen KYC shows first.
3. **Verify your account → Identity & account setup.** Choose BVN (or NIN) and enter
   the test identifier. Fill in names, phone (it must be the verified Zitch number),
   date of birth as `DD/MM/YYYY`, gender and an address of at most 100 characters.
   For NIN, also enter the NIN user ID. Tap **Verify with bank OTP**.
4. Enter the **six-digit 9PSB OTP** and tap **Confirm bank OTP**. The server then
   verifies the OTP, opens the wallet (`open_wallet`), reads it back
   (`wallet_enquiry`) and attaches the **NUBAN**. The NUBAN is the bank's
   10-digit account number, not its internal wallet number.
5. Open **Add Money**. The NUBAN and account name are shown. `bank_tester_status`
   now reports `bank_account.nuban_ready: true` and `nuban_last4`.

If the OTP screen shows *Verification is being processed*, tap **Check
verification status** rather than starting again; a repeat start for the same
identity resumes the existing request.

## 6. Messages and what they mean

| App message | Cause |
|---|---|
| *This bank connection is available to enrolled sandbox testers only* | Not enrolled (section 4) |
| *Banking setup is temporarily unavailable* | A server switch or WaaS credentials (section 2) |
| *Verify your email address and phone number before bank identity verification* | Contact verification incomplete |
| *Use your verified Zitch phone number* | The KYC phone differs from the signup phone |
| *Use the legal name on your Zitch profile* | KYC names differ from the signup names |
| *This identity is already linked to an account* | That test BVN/NIN is bound to another Zitch user; use another test identity |
| *Bank account details need review. Contact support* | `open_wallet` returned a name that differs from the profile (section 3, item 3) |
| *Your bank account is being confirmed.* | Opened, but the read-back did not confirm yet; check status again |
| *The verification or payment service is temporarily unavailable* | Bank call failed; the response `code` names it (e.g. `authentication_failed`, `network_unavailable`) |

Do not run `purge_test_customers` to reset a tester: it deletes **every**
customer on the deployment.

## 7. VAS (bill payments) is not part of this test

The VAS sandbox still rejects Zitch's VAS credentials (see
[9psb-vas-contract.md](9psb-vas-contract.md)). Keep `NINEPSB_VAS_ENABLED=false`.
Once it authenticates, the PDF's sample customer IDs for UAT are: electricity
`12345678910`, betting `34382`, cable TV `7032691481`.

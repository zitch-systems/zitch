# Zitch API (Django) — build & run

Backend for the Zitch mobile app. Implements the exact endpoints the Expo app
calls. Runs in **MOCK mode** until you add aggregator/payment/SMS keys, so the
whole flow is testable with zero external accounts.

## Stack
- Django 5.1 (plain JSON views, no DRF)
- PostgreSQL in prod (SQLite locally)
- Token auth (opaque `access_token` returned in the body, as the app expects)
- Render for hosting

## Run locally
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # tweak if needed; SQLite works out of the box
python manage.py migrate
python manage.py seed_plans     # populate data/cable plans
python manage.py createsuperuser
python manage.py runserver      # http://127.0.0.1:8000  (admin at /admin/)
```

## Point the app at it
In the mobile app, `components/configFiles/apiConfig.tsx`:
```ts
const baseUrl = "http://10.0.2.2:8000";   // Android emulator -> host machine
// device on same wifi: "http://<your-LAN-ip>:8000"
// production:          "https://zitch-api.onrender.com"
```

## Smoke test (mock mode)
```bash
# register -> sends OTP (printed to server log in mock mode via send_sms)
curl -X POST localhost:8000/api/phone_verification/ -H 'Content-Type: application/json' -d '{"phone":"08012345678","email":"a@b.com"}'
# check the OTP in Django admin (OTP table) or server output, then:
curl -X POST localhost:8000/api/verify_otp/ -H 'Content-Type: application/json' -d '{"phone":"08012345678","otp":"<code>"}'
```

## Deploy to Render
1. Push this repo to GitHub (already done).
2. Render dashboard -> **New + -> Blueprint** -> select this repo.
   `render.yaml` creates the web service (rootDir `backend`) + Postgres.
3. After first deploy, set the service env vars (VTpass / Paystack / Termii keys).
4. Create an admin: Render shell -> `python manage.py createsuperuser`.
5. Set the app's `baseUrl` to the Render URL.

> Free tier sleeps after ~15 min and free Postgres expires — fine for testing,
> upgrade both to paid **before** handling real money (webhooks need always-on).

## Endpoints (all POST, JSON)
Auth: `/api/sigin/` · `/api/phone_verification/` · `/api/verify_otp/` ·
`/api/resend_verify_otp/` · `/api/set-password/` · `/api/set-transaction-pin/` ·
`/api/update_info/`
Wallet: `/api/wallet_balance/` · `/api/user-transaction-history/`
Utility: `/api/utility/{buyairtime,get_data_plans,get_data_plans_price,buydata,
get_cable_plans,get_cable_plans_price,validate_iuc,buycable,validate_meter,
buyelectricity}/`

## Before go-live (TODO)
- Wire real VTpass service IDs / variation codes in `utility/providers.py`.
- Add Paystack funding + webhook endpoint and an idempotency guard on webhooks.
- Move auth to an `Authorization: Bearer` header instead of token-in-body.
- Replace seeded plans with the live aggregator catalogue.

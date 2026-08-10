# WhatsApp Flows — secure PIN pad

The PIN never touches the chat. When a money confirm is reached, the bot sends a
**Flow** message; the user types their PIN into a native, masked field inside
WhatsApp, and the submit is delivered **encrypted** to our data-exchange endpoint
(`POST /webhooks/whatsapp/flow`). We decrypt it, verify the PIN server-side (same
brute-force lockout the app/chat use), execute the transaction, and return a
native success screen. Nothing sensitive is ever written to the chat or the
message log.

Until this is fully configured the channel automatically falls back to the
single-use SMS confirmation code — so nothing breaks before you finish the Meta
setup (verify-before-live).

## One-time setup (Meta side)

1. **Business verification.** Complete Meta Business verification for the WABA in
   Meta Business Suite. Flows with an endpoint require a verified business.

2. **Publish the Flow.** In WhatsApp Manager → Flows, create a Flow and paste
   `pin_flow.json` (this folder). Set its **Endpoint URI** to
   `https://<your-host>/webhooks/whatsapp/flow`. Publish it and copy the
   **Flow ID**.

3. **Keys.** Generate an RSA-2048 keypair:
   ```sh
   openssl genrsa -out flow_private.pem 2048
   openssl rsa -in flow_private.pem -pubout -out flow_public.pem
   ```
   Upload the **public** key to the WABA (Business encryption / `whatsapp_business_encryption`
   endpoint). Keep the **private** key secret.

4. **Env.** Set on the backend:
   ```sh
   WHATSAPP_FLOW_ID=<flow id from step 2>
   WHATSAPP_FLOW_PRIVATE_KEY=<contents of flow_private.pem>
   # WHATSAPP_FLOW_PRIVATE_KEY_PASSPHRASE=   # only if you encrypted the key
   ```
   The WhatsApp channel itself must already be live (`WHATSAPP_TOKEN` +
   `WHATSAPP_PHONE_NUMBER_ID`).

Meta health-checks the endpoint with an encrypted `ping`; we answer
`{"status":"active"}`. A key mismatch returns HTTP 421 so Meta refetches the
public key.

## Screens (`pin_flow.json`)

- `PIN_SCREEN` — masked `password` PIN input; its footer submit does the
  `data_exchange` to our endpoint. Re-rendered with an `error` on a wrong PIN.
- `IDENTITY_SCREEN` — masked input for a BVN, a NIN, or the 6-digit email
  confirmation code. Same reasoning as the PIN: WhatsApp has no view-once for
  text and lets only the **sender** delete a message, so anything typed into the
  thread stays in the customer's own history indefinitely. `label` and `summary`
  are supplied per request, so one screen serves all three.
- `EMAIL_SCREEN` — the email address itself, `input-type: email` and
  deliberately **not** masked: an address is not a secret, and masking one the
  customer has to type correctly only breeds typos. It still never reaches the
  chat. Submitting it mails the code and moves to `IDENTITY_SCREEN` on the same
  open Flow.
- `SUCCESS` — terminal screen showing the outcome (`message`).

All four KYC steps therefore behave the same way: phone is the only one whose
code still arrives in the thread, because the SMS itself is the proof of SIM
possession and there is nothing to hide from the customer's own device.

The endpoint drives these dynamically from `whatsapp/flows.py`; keep the screen
ids and field names in sync if you edit the JSON.

> **Re-publish after updating.** `IDENTITY_SCREEN` and `EMAIL_SCREEN` were added
> after the first release, and `PIN_SCREEN` now carries the bank and account on
> their own lines. Meta serves the version published in WhatsApp Manager, not
> this file — until you paste the current JSON and publish again, the Flow send
> fails and the channel falls back to asking in the chat. Unlike the PIN this
> fallback is deliberate rather than fail-closed: refusing service would block
> every signup on a deploy without Flows, which is worse than the number sitting
> in the customer's own thread with instructions to delete it.

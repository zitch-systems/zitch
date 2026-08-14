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
- `VTU_SCREEN` → `VTU_NETWORK` → `VTU_AIRTIME` / `VTU_DATA` → `PIN_CHAIN` — the
  airtime and data ladder, menu option **3**, as one session. The chat version
  asked what to buy, then the network, then the number, then the amount, each
  its own message and each a place to get stuck. `VTU_SCREEN` is the routing
  root; the rest are only ever routed into, which is what keeps the root
  openable.

  Airtime and data split at the third page because their inputs genuinely
  differ — an amount you type versus a plan you pick from **that network's**
  list, fetched at render time. Routing is by `(network, plan_code)`, so a plan
  carried over from another network is refused rather than sent to the provider.

  Same-screen re-renders are correct here and are *not* the defect the PIN pages
  have: these fields are visible, so a refused value staying in the box helps
  rather than traps.

- `SUCCESS` — terminal screen showing the outcome as a `status` heading
  (`✅ Successful` / `⏳ Pending` / `❌ Not completed` / `Done`) over the
  `message` detail. The heading is the point: the screen used to render only the
  sentence, so a settled transfer, a queued one and a refused one all looked
  alike at a glance. `whatsapp/router.py`'s `Outcome` carries the tag from
  whichever executor produced the line.

  The endpoint waits up to **`WHATSAPP_FLOW_SETTLE_WAIT`** seconds (default 3,
  hard-capped at 6, `0` disables) for the ledger row to become terminal before
  answering, so a rail that settles quickly closes the Flow on `✅ Successful`
  and a refusal closes on `❌ Not completed` — the outcome the customer should
  see *before* the screen closes, not only in a chat message they may scroll
  past. Without that wait `✅` was unreachable in production: every money Flow
  answered the instant the job was queued and therefore always said `⏳ Pending`.

  The wait is deliberately small and deliberately optional. Meta gives a
  data-exchange roughly 10 seconds and shows *"Couldn't load content. Try again
  later."* past that — the failure that moving execution off the request thread
  was introduced to fix — and the wait also occupies one of the web dyno's eight
  gunicorn threads, which serve `/healthz` from the same pool. It changes nothing
  about the payment: it is queued and executing either way, and the worker sends
  the receipt regardless.

  **`⏳ Pending` remains the honest answer whenever the rail is still working.** A
  screen that said "successful" for a payment nobody has confirmed would be
  asserting something not established — on a banking channel, the worst available
  lie. The settled result always arrives in the chat as the receipt.

All four KYC steps therefore behave the same way: phone is the only one whose
code still arrives in the thread, because the SMS itself is the proof of SIM
possession and there is nothing to hide from the customer's own device.

### What the Flow JSON cannot do

Asked for and deliberately absent, so nobody re-litigates them from scratch:

- **Auto-closing the terminal screen after N seconds.** Flow JSON's actions are
  `navigate`, `complete`, `data_exchange`, `update_data` and `open_url`, and all
  five are user-triggered. There is no timer, delay, or auto-navigate primitive,
  so `SUCCESS` closes when the customer taps **Done**.
- **A spinner / rotating logo while a submit is in flight.** There is no
  animation, spinner, or progress component. WhatsApp's own Footer button
  already renders a native busy state for the duration of a `data_exchange`,
  which is the only loading indicator available.

Both would need Meta to add components; neither is achievable in this file.

The endpoint drives these dynamically from `whatsapp/flows.py`; keep the screen
ids and field names in sync if you edit the JSON.

## Re-publishing (`manage.py publish_flow`)

Meta serves the version published in WhatsApp Manager, not this file, so every
edit here needs a re-publish. That used to be a manual paste, which meant the
code and the Flow shipped at different times — and they are a **contract**, not
two copies: the endpoint must answer with exactly the properties the published
screen declares. A mismatch shows the customer *"Couldn't load content. Try
again later."* on every ending of the Flow, indistinguishable from a timeout.

Run it where the token already is (a Render shell on the API service):

```sh
python manage.py publish_flow --dry-run   # compare this file against Meta, send nothing
python manage.py publish_flow             # replace the DRAFT, print validation errors
python manage.py publish_flow --publish   # ...and make it live
```

Upload and publish are two steps on purpose: uploading only replaces the draft,
so it is safe at any time and returns the validation errors Meta would refuse a
publish on — the risky half can be read before anyone commits to it. The command
refuses to publish from a host that is not `WHATSAPP_MODE=live`, and refuses to
publish at all when validation returned anything.

`--dry-run` runs `providers.published_flow_report()`, which compares both the
screen ids **and** each screen's declared `data` properties — id-only comparison
reported a perfectly healthy Flow throughout exactly the outage described above.
It is a three-hop Graph read, so it is deliberately *not* wired into `/healthz`;
run it from the command when you want the answer.

> **Re-publish after updating.** `IDENTITY_SCREEN` and `EMAIL_SCREEN` were added
> after the first release, and `PIN_SCREEN` now carries the bank and account on
> their own lines. Meta serves the version published in WhatsApp Manager, not
> this file — until you paste the current JSON and publish again, the Flow send
> fails and the channel falls back to asking in the chat. Unlike the PIN this
> fallback is deliberate rather than fail-closed: refusing service would block
> every signup on a deploy without Flows, which is worse than the number sitting
> in the customer's own thread with instructions to delete it.

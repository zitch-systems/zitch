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

- **Balance and narration on the PIN pages.** `PIN_SCREEN`, `PIN_CHAIN` and
  `PIN_RETRY` each declare two further properties: `balance`, rendered above the
  amount, and `narration`, rendered under the routing detail. The balance is
  there because this is the last screen before the money goes and deciding
  whether to send ₦50,000 needs the number in front of you — without it the only
  way to check was to abandon the payment, ask, and start again.

  It renders at the **top of the screen, not the top-right corner**: a
  `SingleColumnLayout` stacks its children vertically and this Flow JSON version
  has no row, column or alignment primitive, so a corner is not expressible here.

  `TRANSFER_FORM`, `VTU_AIRTIME` and `VTU_DATA` carry the optional `narration`
  input that fills it. The signup pair (`PIN_CONFIRM` / `PIN_CONFIRM_RETRY`) has
  neither: it re-enters a PIN being created and there is no transaction behind
  it.

  Both are supplied on **every** send and every endpoint answer, including the
  signup PIN and the PIN reset, which pass empty strings. Declared-but-absent is
  the contract mismatch described under *Re-publishing* below, and it is why
  every PIN-screen send now goes through `router.pin_screen_send_data()` rather
  than a hand-written dict — that drift has already broken signup once.

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

## Release 2 screens

Added to `pin_flow.json` additively (no existing screen, field or route was
changed or removed; Flow JSON version stays **6.0** — the menu uses
`RadioButtonsGroup` + `data_exchange`, not `NavigationList`, which would need a
newer version). Handlers live in `whatsapp/release2.py`. Every one is behind a
`WHATSAPP_FLOW` flag that defaults **off**; with a flag off the code never sends
or answers that screen.

| Flag (env var) | Screens | What it does |
|---|---|---|
| `WHATSAPP_FLOW_DETAILS_SCREEN` | `SIGNUP_DETAILS` (root), `DETAILS_CHAIN` (from `PRIVACY_NOTICE`) | First name, surname, phone and email on one page, "Step 1 of 5". Same validators as `SIGNUP_SCREEN` + `SIGNUP_PHONE`. |
| `WHATSAPP_FLOW_VERIFY_PASSWORD_SCREEN` | `VERIFY_PASSWORD` | When the typed phone is not the WhatsApp number: the SMS code, "Create a password", "Confirm password" and a "Didn't get it? Resend code" link on one page, "Step 2 of 5". Same number: the existing `SIGNUP_PASSWORD` page (this flag implies the password step). Then the PIN as today. |
| `WHATSAPP_FLOW_LOGIN_ONE_SCREEN` | `LOGIN_SCREEN` (root), `LOGIN_RETRY` | Existing-account sign-in: email code + PIN on one page. Wrong code → `LOGIN_RETRY`; right code + wrong PIN → `PIN_RETRY`. Same budgets as before (2 codes, 2 PINs). |
| `WHATSAPP_FLOW_BILL_FORM` | `BILL_SCREEN` (root), `BILL_CHAIN` (from `MENU`), `BILL_AMOUNT` | Menu 4 "Pay a bill" sends a "Pay a bill" Flow button **and** the chat sub-menu (typing 1/2 or a disco name keeps the chat path). Disco + Prepaid/Postpaid + meter → the chat's own meter check → "✓ Meter verified · NAME", address, amount → `PIN_CHAIN` → `_exec_electricity`. **Electricity only**; cable stays in the chat this release (there is no betting product in the chat). |
| `WHATSAPP_FLOW_MENU_FORM` | `MENU` (root), `TRANSFER_CHAIN`, `VTU_CHAIN` (+ `BILL_CHAIN`) | The chat menu sends a "What would you like to do?" card with a "☰ Menu" button, then the numbered list as before. Send money / Airtime & data / Pay a bill (with `BILL_FORM` on) open their form inside the same Flow; every other item closes the Flow and is answered in the chat as if typed (via the inbound queue in production). |

No flag is needed for the confirm layout: the PIN pages already render
"Available balance ₦…" above the amount heading; every line under it is now
"Label: value" (`To: JOHN DOE`, `Disco: Ikeja`, …), and a settled payment's
outcome ends with "Receipt sent to your chat." These are data changes only.

## Release 2 go-live

Order matters: code first (flags off), then the Flow, then the flags.

1. **Deploy this code** with every Release 2 flag unset. Nothing changes for
   customers: the currently published Flow keeps serving every existing screen.

2. **Compute the reviewed SHA256** of the exact file being published — the
   canonical form `deploy_whatsapp_flow` checks (sorted keys, no whitespace):
   ```sh
   cd backend
   python -c 'import hashlib,json; d=json.load(open("whatsapp/flow_assets/pin_flow.json")); print(hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest())'
   ```
   (`python manage.py publish_flow --dry-run` prints the same value as "Local
   contract SHA-256".)

3. **Create the draft** (one API build with these set; `build.sh` runs
   `deploy_whatsapp_flow` only when `WHATSAPP_FLOW_DEPLOY_OPERATION` is set):
   ```sh
   WHATSAPP_FLOW_DEPLOY_OPERATION=create-draft
   WHATSAPP_FLOW_DEPLOY_REQUEST=release2-<date>        # 8–64 of [A-Za-z0-9_-]; names the draft "Zitch Partnership <REQUEST>"
   WHATSAPP_FLOW_DEPLOY_SHA256=<digest from step 2>
   WHATSAPP_FLOW_DEPLOY_ENDPOINT_URI=https://<api-host>/webhooks/whatsapp/flow
   ```
   The build log prints `Reviewed Flow: SHA256 …; 35 screens.`, the new draft's
   Flow ID and Meta's validation errors. Fix any error before going on. A retry
   with the same REQUEST reuses the same draft rather than creating a second.

4. **Review the draft** in WhatsApp Manager → Flows (preview each new screen).

5. **Publish it** (another build; same REQUEST, SHA256 and ENDPOINT_URI):
   ```sh
   WHATSAPP_FLOW_DEPLOY_OPERATION=publish
   WHATSAPP_FLOW_DEPLOY_FLOW_ID=<draft Flow ID from step 3>
   ```
   The log ends `Flow <id> published; SHA256 …; 35 screens. WHATSAPP_FLOW_ID
   remains unchanged.`

6. **Switch the channel to the new Flow**: set `WHATSAPP_FLOW_ID=<new Flow ID>`
   and clear every `WHATSAPP_FLOW_DEPLOY_*` variable, then redeploy. Every
   existing screen is in the new Flow unchanged, so the flags-off paths keep
   working. Confirm with `python manage.py publish_flow --dry-run` (or
   `whatsapp_flow_published` on `/healthz`): no missing or drifted screens.

7. **Turn the flags on, one at a time**, testing each on a real phone before the
   next (each takes effect on the next restart):
   ```sh
   WHATSAPP_FLOW_LOGIN_ONE_SCREEN=true
   WHATSAPP_FLOW_DETAILS_SCREEN=true
   WHATSAPP_FLOW_VERIFY_PASSWORD_SCREEN=true
   WHATSAPP_FLOW_BILL_FORM=true
   WHATSAPP_FLOW_MENU_FORM=true
   ```
   Rollback is the same variable set back to `false` (or unset). A session
   already open on a new screen is dispatched by its stored state, not the flag,
   so it finishes normally.

8. *(Optional)* retire the previous Flow with
   `WHATSAPP_FLOW_DEPLOY_OPERATION=deprecate`, `…_FLOW_ID=<old>`,
   `…_REPLACEMENT_FLOW_ID=<new>` — only once the new ID is selected, and only for
   an old Flow that still carries the retired setup screens (the command checks).

> **Re-publish after updating.** `IDENTITY_SCREEN` and `EMAIL_SCREEN` were added
> after the first release, and `PIN_SCREEN` now carries the bank and account on
> their own lines. Meta serves the version published in WhatsApp Manager, not
> this file — until you paste the current JSON and publish again, the Flow send
> fails and the channel falls back to asking in the chat. Unlike the PIN this
> fallback is deliberate rather than fail-closed: refusing service would block
> every signup on a deploy without Flows, which is worse than the number sitting
> in the customer's own thread with instructions to delete it.

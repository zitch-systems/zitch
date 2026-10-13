# Publish the reviewed Partnership Flow

The API build is the only service that runs this hook, after database migrations.
Ordinary builds do nothing. It never changes `WHATSAPP_FLOW_ID` or sends messages.
The operation uses a PostgreSQL session advisory lock, without a transaction held
during Meta requests. Listing is limited to ten pages; failures stop the build.

1. Set these **temporary API environment variables**, then deploy the reviewed commit:

   ```text
   WHATSAPP_FLOW_DEPLOY_OPERATION=create-draft
   WHATSAPP_FLOW_DEPLOY_REQUEST=partnership-20261013-01
   WHATSAPP_FLOW_DEPLOY_SHA256=9a736841bd71c5a8d2e58cf6780793941e6f67117b67dfd224f5344fb34d5dae
   WHATSAPP_FLOW_DEPLOY_ENDPOINT_URI=<existing HTTPS Flow data-exchange endpoint>
   ```

   The existing `WHATSAPP_WABA_ID`, channel credentials and database must be set.
   Do not set `WHATSAPP_FLOW_DEPLOY_FLOW_ID` for draft preparation. The request
   selects the deterministic name `Zitch Partnership partnership-20261013-01`.
   Retries reuse its single draft; ambiguous matches and published names fail.

2. Review the build log's draft ID, full JSON SHA-256 and 24-screen count. The
   command reads the entire uploaded asset back and requires an exact digest,
   correct endpoint and no validation errors. Confirm the removed account setup
   screens are absent. Draft preparation does not publish.

3. Preserve the same request, SHA and endpoint. Change the operation to `publish`
   and set `WHATSAPP_FLOW_DEPLOY_FLOW_ID` to the reviewed numeric ID, then deploy.
   This operation validates the existing remote asset before publication and
   performs **no upload**. A repeated publish only verifies the already-published
   contract. A mismatch blocks publication; inspect Meta before retrying.

4. After the log confirms publication and exact digest, update `WHATSAPP_FLOW_ID`
   on the API and worker to that ID and deploy their configuration. Clear the
   operation before that configuration deployment; clear all temporary
   `WHATSAPP_FLOW_DEPLOY_*` variables before ordinary deployments resume.
   Verify the diagnostic reports the new ID, `published`, `contract_matches=true`,
   and no missing, drifted or unexpected screens.
   The new worker must also emit `whatsapp_worker_flow flow_id=<new ID>
   contract_sha256=<reviewed SHA>` in its **runtime startup log**. This line reads
   only local configuration and the shipped asset; it makes no Meta request and
   does not print channel credentials.

5. Only after **both API and worker** use the new ID, an optional separate
   operation can retire an obsolete published Flow. Keep the reviewed request,
   SHA and endpoint, then set `WHATSAPP_FLOW_DEPLOY_OPERATION=deprecate`,
   `WHATSAPP_FLOW_DEPLOY_FLOW_ID=<one explicit old Flow ID>`, and
   `WHATSAPP_FLOW_DEPLOY_REPLACEMENT_FLOW_ID=<published new Flow ID>`.
   The hook requires the API's selected ID to be that replacement, verifies its
   exact published contract again, confirms both IDs belong to the WABA, and
   reads the old asset to prove it contains retired setup screens before calling
   Meta's deprecation endpoint. A PIN-only Flow is refused. Deprecation is an
   irreversible provider action; it is never part of draft creation or publishing.
   The operator must verify the worker selection; this API hook cannot read
   another Render service's environment. Clear the operation after its log
   confirms `DEPRECATED`. Repeat only for explicitly reviewed obsolete IDs.
   Endpoint reference: [Meta's WhatsApp Cloud API collection](https://www.postman.com/meta/whatsapp-business-platform/request/uje0iad/deprecate-flow).

The SHA is canonical JSON: sorted keys with separators `,` and `:`. A changed
asset requires a new review and digest. Never reuse a request name for a different
reviewed contract. An unknown provider outcome is not automatically retried within
one build; inspect the deterministic named Flow before restarting the operation.
A durable review/creation-attempt marker prevents a second create if Meta's list
has not yet exposed a previous attempt. The marker contains only the request
binding hashes and attempt flag. A visible matching draft remains reusable.

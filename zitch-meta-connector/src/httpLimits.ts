/**
 * Transport limits shared by the Express parser and tool schemas.
 *
 * The production Flow asset is currently ~329 KB because its logo is embedded
 * as base64. Express defaults to 100 KB (the connector previously used 256 KB),
 * which rejected a valid update_whatsapp_flow_json MCP call before zod or Meta
 * could see it. One MiB leaves deliberate growth room while remaining a hard,
 * modest ceiling for an authenticated maintenance service.
 */
export const JSON_BODY_LIMIT_BYTES = 1024 * 1024;

/** Leave room inside the one-MiB JSON-RPC envelope for method metadata and JSON
 * string escaping. The checked-in Flow is tested against both bounds. */
export const MAX_FLOW_JSON_CHARS = 750_000;

// Default to the production API on the custom domain. Override in local dev by
// setting EXPO_PUBLIC_API_URL before `expo start`, e.g.:
//   PowerShell:  $env:EXPO_PUBLIC_API_URL = "http://10.0.2.2:8000"   (Android emulator)
//                $env:EXPO_PUBLIC_API_URL = "http://localhost:8000"   (web / iOS sim)
//                $env:EXPO_PUBLIC_API_URL = "http://<LAN-IP>:8000"    (physical device on Wi-Fi)
// api.zitch.ng is the only release origin. The Render-assigned origin deliberately
// is not a client fallback: retrying there would bypass the edge/WAF controls.
export const PRIMARY_API_URL = "https://api.zitch.ng";
const configured = process.env.EXPO_PUBLIC_API_URL ?? PRIMARY_API_URL;

// Defence in depth: a release build must never talk to the API over plaintext
// HTTP — that would expose the bearer token, transaction PIN and BVN/NIN in
// transit. In dev (__DEV__) http://localhost / LAN IPs stay allowed for the
// emulator and on-device testing.
const RELEASE_ORIGINS = new Set([PRIMARY_API_URL]);
const PRIVATE_HOST =
  /^(localhost|127(?:\.\d{1,3}){3}|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})$/i;

/**
 * Resolve the build-time override without letting a release build send session
 * tokens, transaction PINs, or KYC data to an arbitrary HTTPS host.
 */
export function resolveApiBaseUrl(value: string, dev = __DEV__): string {
  try {
    const candidate = new URL((value || "").trim());
    const normalized = candidate.toString().replace(/\/$/, "");

    if (dev) {
      if (candidate.protocol === "https:") return normalized;
      if (candidate.protocol === "http:" && PRIVATE_HOST.test(candidate.hostname)) return normalized;
    }

    const origin = candidate.origin.toLowerCase();
    if (candidate.protocol === "https:" && RELEASE_ORIGINS.has(origin)) return origin;
  } catch {
    // Invalid or relative build-time values fail closed to the production API.
  }
  return PRIMARY_API_URL;
}

const baseUrl = resolveApiBaseUrl(configured);

export default baseUrl;


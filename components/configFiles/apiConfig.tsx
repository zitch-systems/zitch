// Default to the production API on the custom domain. Override in local dev by
// setting EXPO_PUBLIC_API_URL before `expo start`, e.g.:
//   PowerShell:  $env:EXPO_PUBLIC_API_URL = "http://10.0.2.2:8000"   (Android emulator)
//                $env:EXPO_PUBLIC_API_URL = "http://localhost:8000"   (web / iOS sim)
//                $env:EXPO_PUBLIC_API_URL = "http://<LAN-IP>:8000"    (physical device on Wi-Fi)
// Note: api.zitch.ng must be live (Render custom domain + DNS + TLS cert) before
// shipping a build on this default; https://zitch-api.onrender.com also still works.
const configured = process.env.EXPO_PUBLIC_API_URL ?? "https://api.zitch.ng";

// Defence in depth: a release build must never talk to the API over plaintext
// HTTP — that would expose the bearer token, transaction PIN and BVN/NIN in
// transit. In dev (__DEV__) http://localhost / LAN IPs stay allowed for the
// emulator and on-device testing.
const baseUrl =
  !__DEV__ && configured.startsWith("http://") ? "https://api.zitch.ng" : configured;

export default baseUrl;
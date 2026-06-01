// Default to the Render production API. Override in local dev by setting
// EXPO_PUBLIC_API_URL before `expo start`, e.g.:
//   PowerShell:  $env:EXPO_PUBLIC_API_URL = "http://10.0.2.2:8000"   (Android emulator)
//                $env:EXPO_PUBLIC_API_URL = "http://localhost:8000"   (web / iOS sim)
//                $env:EXPO_PUBLIC_API_URL = "http://<LAN-IP>:8000"    (physical device on Wi-Fi)
const baseUrl = process.env.EXPO_PUBLIC_API_URL ?? "https://zitch-api.onrender.com";

export default baseUrl;
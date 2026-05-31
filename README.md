# Zitch

Zitch is a Nigerian fintech / utility-payments + wallet mobile app: airtime, data,
cable TV, electricity, exams, loans, transfers and a wallet. Built with
[Expo](https://expo.dev) (SDK 51), [expo-router](https://docs.expo.dev/router/introduction)
file-based routing, and [NativeWind](https://www.nativewind.dev/).

## Tech stack

- **Expo SDK 51** / React Native 0.74
- **expo-router v3** — file-based routing (route groups under `app/`)
- **NativeWind v2** — Tailwind-style styling (`tailwind.config.js`)
- **expo-secure-store** — encrypted storage for the access token
- **AsyncStorage** — non-sensitive local state

## Getting started

1. Install dependencies

   ```bash
   npm install
   ```

2. Start the app

   ```bash
   npx expo start
   ```

   Then open it in a development build, Android emulator, iOS simulator, or Expo Go.

## Project structure

```
app/
  index.tsx              # landing screen
  (auth)/                # onboarding & auth (signin, register, otp, setpin, setpassword, ...)
  (homepage)/            # authenticated tabs (home, wallet, loan, profile) — gated by AuthGuard
  (servicesscreen)/      # service flows (buyairtime, buydata, buycable, buyelectricity, ...) — gated
components/              # reusable UI (CustomButtons, CustomField, AuthGuard, ComingSoonView)
components/configFiles/  # apiConfig (base URL) and links (legal URLs)
constants/               # images, icons, colors
lib/secureStore.ts       # token storage (SecureStore on native, AsyncStorage on web)
docs/design_handoff_zitch_revamp/   # design reference / prototype (NOT shipped code)
```

## Configuration

- **API base URL:** `components/configFiles/apiConfig.tsx`
- **Legal links:** `components/configFiles/links.ts` (placeholders — replace with real URLs)
- **Design tokens:** mirrored into `tailwind.config.js` from
  `docs/design_handoff_zitch_revamp/assets/tokens.css`

## Testing

```bash
npm test        # single run
npm run test:watch
```

## Notes

- Several service flows (loans, exams, send money, biometric setup) are placeholders that
  render a "Coming Soon" screen until implemented.
- The `docs/design_handoff_zitch_revamp/` bundle is an HTML/React prototype used as the
  visual source of truth for the planned revamp. It is **not** production code.

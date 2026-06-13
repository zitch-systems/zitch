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

## Develop in GitHub Codespaces

This repo ships a dev container (`.devcontainer/`). In GitHub: **Code ▸ Codespaces
▸ Create codespace on this branch**. On first boot it auto-installs the backend
(Python deps, migrations, seeded plans) and the app (`npm install`).

Once it's ready, in the Codespace terminal:

```bash
# Backend (Django) — http://localhost:8000  (admin at /admin/)
cd backend && python manage.py createsuperuser && python manage.py runserver 0.0.0.0:8000

# App (Expo Metro) — new terminal
npx expo start

# Android APK (needs your Expo login)
npx eas-cli login && npx eas-cli init && npx eas-cli build -p android --profile preview
```

> Codespaces runs the **dev environment**, not production. Deploy the backend to
> **Render** via `backend/render.yaml` (Render dashboard ▸ New ▸ Blueprint).

## Getting started (local)

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

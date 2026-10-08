# Zitch

Zitch is a Nigerian fintech / utility-payments + wallet mobile app: airtime, data,
cable TV, electricity, exams, loans, transfers and a wallet. Built with
[Expo](https://expo.dev) (SDK 51), [expo-router](https://docs.expo.dev/router/introduction)
file-based routing, and [NativeWind](https://www.nativewind.dev/).

## Download the Android test app

Open [GitHub Releases](https://github.com/zitch-systems/zitch/releases) on your Android phone, open the newest **Zitch Android test APK** release, then download **Zitch-Android-Test.apk** under **Assets** and open it to install. The source-code ZIP files are not needed. Allow installation for the browser/download app if Android asks.

These are release-mode internal test builds signed with the existing Android test key. They connect to `https://api.zitch.ng`; account activation and service availability follow server controls. They are not Play Store production releases. If an existing installation reports a signing conflict, keep it and report the conflict before removing it.

Each tagged test release must pass the exact source revision's four CI suites, APK certificate/manifest verification, and an Android emulator startup/sign-in check. The release includes a SHA-256 checksum and build metadata. Production signing, push credentials, physical-device acceptance and bank settlement acceptance remain separate requirements.

Maintainers can trigger publication with a `zitch-android-test-*` tag, or by creating a branch named `release/zitch-android-test-*` at the exact reviewed commit on `main` after all four CI suites pass. For example, `release/zitch-android-test-20261008` publishes the tag `zitch-android-test-20261008`. The branch route listens to GitHub's `create` event, including API branch creation; subsequent pushes do not start another release. Do not add commits to the release branch: the workflow requires its commit to already be merged into `main` and green in CI. Both triggers use the same APK verification and emulator gates. The branch trigger creates its tag at the tested commit; an existing tag must match that commit. Use one trigger per release name; existing releases are never overwritten.

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
> **Render** using the reviewed root `render.frankfurt.yaml` configuration. For the
> existing workspace, follow the [billing restoration order](docs/frankfurt-billing-restoration-2026-10-03.md)
> and update mapped services individually; an unreviewed Blueprint sync may create
> duplicate resources. Keep worker and cron holds in place before billing restoration.

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
lib/secureStore.ts       # token storage (SecureStore on native, memory only on web)
docs/design_handoff_zitch_revamp/   # design reference / prototype (NOT shipped code)
```

## Configuration

- **API base URL:** `components/configFiles/apiConfig.tsx`
- **Legal links:** `components/configFiles/links.ts`
- **Design tokens:** mirrored into `tailwind.config.js` from
  `docs/design_handoff_zitch_revamp/assets/tokens.css`

## Testing

```bash
npm test        # single run
npm run test:watch
```

## Notes

- Feature availability is determined by the backend and provider readiness. In particular, VAS transfers and VAS-funded bills remain disabled until bank acceptance; an APK build does not activate them.
- The `docs/design_handoff_zitch_revamp/` bundle is an HTML/React prototype used as the
  visual source of truth for the planned revamp. It is **not** production code.

# Mobile push and signing setup

The existing Expo project is owned by `zitch`, with project ID
`a2e8a1ed-506f-4fc8-9783-210c8aab1060`. Both app identifiers are
`com.zitch.app`. Reuse this project and the existing signing identities.

As checked on 2026-10-13, this workspace has no Firebase client file, authenticated
Expo session, Apple signing credentials, or APNs credential available. The internal
Android 1.0.5 release has no Firebase configuration. Registration and delivery are
separate checks; a successful registration response proves neither FCM nor APNs
delivery. No live notification was sent during this audit.

The follow-up Android source version is 1.0.6 (version code 14). Its unavailable
push state is explicit in Settings when Firebase configuration is absent.

## Android

1. In the existing Zitch Firebase project, select the Android app for
   `com.zitch.app` and obtain its `google-services.json` client configuration.
   Supply it as the EAS file variable `GOOGLE_SERVICES_JSON`, or as the GitHub
   release secret `ANDROID_GOOGLE_SERVICES_JSON_BASE64`. The release workflow
   materializes and validates the latter before prebuild. Do not put service
   account private keys in this client file or in Expo `extra`.
2. Associate the matching FCM v1 service account with this application in the
   existing Expo project's Credentials page (Android → FCM V1 service account
   key). Use an existing authorized service account where possible. This secret
   is separate from the client file embedded in the app.
3. If Firebase API-key application restrictions are enabled, register the SHA-1
   of the certificate signing the installed app. Google Play uses the Play app
   signing certificate; internal APKs use their own recorded certificate. Allow
   the Firebase Installations and FCM Registration APIs as required by Expo.
4. Validate the local configuration without displaying it:
   `ZITCH_REQUIRE_PUSH_CONFIG=true node scripts/android-google-services.cjs`.
   The production Android EAS profile sets this gate automatically. A missing,
   malformed or wrong-package client file fails before a production build.
5. Build a new native binary after the client file is supplied. The existing APK
   cannot gain native Firebase configuration through a server deployment. Inspect
   its release `build-info.json`: `android_push.firebase_config_present` must be
   true, with the intended commit and signing certificate.

`extra.push.androidConfigured` contains only a public boolean. An internal build
without configuration remains usable, but Settings reports that push is
unavailable and the app skips native permissions, token creation and server
registration. iOS does not use this Android gate. In-app Notifications remain
available on both platforms.

After credentials are configured, delivery requires an explicitly authorized
physical-device check on the exact signed build: permission, token registration,
receipt, foreground/background presentation and tap through sign-in. Never mark
`live_delivery_verified` true from a token or an emulator UI smoke test alone.

## iOS

An authorized member of the existing Expo and Apple Developer teams must inspect
or provision the existing distribution certificate, provisioning profile and APNs
key using the Expo project's Credentials page or `eas credentials --platform ios`.
No replacement Apple identity, team ID, APNs key or App Store listing has been
created by this change. Keep downloaded secrets outside the repository;
`credentials.json` and the local `credentials/` directory are ignored.

The installed `expo-notifications` config plugin supplies the APNs entitlement
during native generation. EAS must pair it with the correct Apple app identifier,
team and provisioning profile. Android Firebase-file validation intentionally
applies only to `build.production.android.env` so it cannot block an iOS build.

Once account access is supplied, build with the existing production EAS profile
for iOS and retain the build ID, source revision, bundle version and certificate
metadata. On a Mac, verify the downloaded IPA's app signature with `codesign
--verify --deep --strict`, inspect its entitlements with `codesign -d
--entitlements :-`, and decode `embedded.mobileprovision` using `security cms -D
-i`. The application identifier must match the authorized team and `com.zitch.app`;
check certificate/profile expiry, distribution type, `get-task-allow` and the
appropriate APNs environment. A Store/TestFlight distribution must not be a
simulator or development-signed build. Install through the intended distribution
channel and complete the separately authorized physical-device push check before
claiming iOS delivery is ready.

## References

- [Expo FCM v1 credentials and client configuration](https://docs.expo.dev/push-notifications/fcm-credentials/)
- [EAS build configuration and platform-specific environment](https://docs.expo.dev/eas/json/)
- [Expo managed signing credentials](https://docs.expo.dev/app-signing/managed-credentials/)
- [EAS environment and file variables](https://docs.expo.dev/eas/environment-variables/)

const { resolveGoogleServices } = require('./scripts/android-google-services.cjs');

// Sideload test builds (ZITCH_ANDROID_VARIANT=test, set only by the Android APK
// workflow) are their own app, "Zitch Test" (com.zitch.app.test), so they install
// beside the Play Store app instead of failing with "App not installed".
const TEST_VARIANT = process.env.ZITCH_ANDROID_VARIANT === 'test';

module.exports = ({ config }) => {
  const android = TEST_VARIANT ? { ...config.android, package: 'com.zitch.app.test' } : config.android;
  // Push is registered for the store package only, so a test build has none.
  const googleServicesFile = TEST_VARIANT ? undefined : resolveGoogleServices(android.package);
  return {
    ...config,
    ...(TEST_VARIANT ? { name: 'Zitch Test', scheme: 'zitchtest' } : {}),
    android: { ...android, ...(googleServicesFile ? { googleServicesFile } : {}) },
    // Public build capability only. Never embed Firebase/server credentials.
    // Having a native client permits registration; it does not certify delivery.
    extra: {
      ...config.extra,
      push: { androidConfigured: Boolean(googleServicesFile) },
    },
  };
};

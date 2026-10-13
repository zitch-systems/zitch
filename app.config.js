const { resolveGoogleServices } = require('./scripts/android-google-services.cjs');

module.exports = ({ config }) => {
  const googleServicesFile = resolveGoogleServices(config.android.package);
  return {
    ...config,
    android: { ...config.android, ...(googleServicesFile ? { googleServicesFile } : {}) },
    // Public build capability only. Never embed Firebase/server credentials.
    // Having a native client permits registration; it does not certify delivery.
    extra: {
      ...config.extra,
      push: { androidConfigured: Boolean(googleServicesFile) },
    },
  };
};

const { resolveGoogleServices } = require('./scripts/android-google-services.cjs');

module.exports = ({ config }) => {
  const googleServicesFile = resolveGoogleServices(config.android.package);
  return {
    ...config,
    android: { ...config.android, ...(googleServicesFile ? { googleServicesFile } : {}) },
  };
};

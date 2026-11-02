// Expo config plugin for Android application-level transport and backup policy.
//
// Expo SDK 51 supports `android.allowBackup` in app.json, but it does not expose
// `usesCleartextTraffic` in the config schema. Native projects are generated at
// build time, so both invariants are enforced here in the generated manifest.

const { withAndroidManifest } = require('expo/config-plugins');

const FOLD_CONFIG_CHANGES = ['keyboard', 'keyboardHidden', 'orientation', 'screenSize',
  'smallestScreenSize', 'screenLayout', 'uiMode'];

const BLOCKED_PERMISSIONS = new Set([
  'android.permission.SYSTEM_ALERT_WINDOW',
]);

/**
 * Apply security-sensitive <application> attributes to a parsed manifest.
 * Exported separately so policy drift can be caught without running prebuild.
 *
 * @param {object} androidManifest parsed AndroidManifest.xml
 * @returns {object} the same manifest after mutation
 */
function applyAndroidSecurity(androidManifest) {
  const app = androidManifest?.manifest?.application?.[0];
  if (!app) {
    throw new Error('withAndroidSecurity: no <application> in AndroidManifest.xml');
  }

  app.$ = app.$ || {};
  app.$['android:allowBackup'] = 'false';
  app.$['android:usesCleartextTraffic'] = 'false';

  // Expo/dev-menu dependencies have historically contributed overlay permission
  // declarations during manifest merging. Deleting the app-level declaration is
  // insufficient: a library manifest could add it back later. Keep one explicit
  // tools:node="remove" marker for every blocked permission so Android's manifest
  // merger removes transitive declarations from the final release manifest too.
  const declared = Array.isArray(androidManifest.manifest['uses-permission'])
    ? androidManifest.manifest['uses-permission']
    : [];
  const kept = declared.filter(
    (entry) => !BLOCKED_PERMISSIONS.has(entry?.$?.['android:name'])
  );
  for (const permission of BLOCKED_PERMISSIONS) {
    kept.push({
      $: {
        'android:name': permission,
        'tools:node': 'remove',
      },
    });
  }
  androidManifest.manifest['uses-permission'] = kept;

  // Foldables: unfolding changes the screen size. Without handling these changes
  // Android recreates the activity, the app restarts its JavaScript, and a
  // restart counts as a fresh launch that must sign in again. Handle them in
  // place so opening a Fold keeps the customer where they were.
  const activities = Array.isArray(app.activity) ? app.activity : [];
  const main = activities.find((a) => a?.$?.['android:name'] === '.MainActivity');
  if (main) {
    const current = String(main.$['android:configChanges'] || '').split('|').filter(Boolean);
    for (const change of FOLD_CONFIG_CHANGES) if (!current.includes(change)) current.push(change);
    main.$['android:configChanges'] = current.join('|');
    main.$['android:resizeableActivity'] = 'true';
  }
  return androidManifest;
}

const withAndroidSecurity = (config) =>
  withAndroidManifest(config, (cfg) => {
    cfg.modResults = applyAndroidSecurity(cfg.modResults);
    return cfg;
  });

module.exports = withAndroidSecurity;
module.exports.applyAndroidSecurity = applyAndroidSecurity;
module.exports.FOLD_CONFIG_CHANGES = FOLD_CONFIG_CHANGES;

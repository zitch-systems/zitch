// Expo config plugin: give the Android `release` build type a real signing config.
//
// Why a plugin and not an edit to android/app/build.gradle: the native project is
// generated (`expo prebuild --clean`) and is not committed, so any hand edit is
// erased on the next build. The signing change has to be expressed as a mod.
//
// What the SDK 54 template ships (expo-template-bare-minimum, app/build.gradle):
//
//     release {
//         // Caution! In production, you need to generate your own keystore file.
//         signingConfig signingConfigs.debug
//     }
//
// So `assembleRelease` produces a release-mode binary signed with the *debug*
// key — installable for sideload testing, rejected by Play. That is the state the
// 25 July audit recorded, and it is why this exists.
//
// After this plugin, the key is chosen by whether the ZITCH_UPLOAD_* Gradle
// properties are set:
//
//   set   -> the upload keystore (the Play path; CI injects them)
//   unset -> the debug key, exactly as before (the preview/sideload path)
//
// Failing *open* to the debug key is deliberate: a developer running
// `expo run:android --variant release` locally must not need the production
// keystore. What must never happen is shipping a debug-signed build without
// noticing, so the chosen key is printed at configure time and the CI workflow
// that produces the Play artifact verifies the signer certificate afterwards.

const { withAppBuildGradle } = require('expo/config-plugins');

// Marker so the transform is idempotent — prebuild can apply mods more than once.
const MARKER = 'ZITCH_UPLOAD_STORE_FILE';

const SIGNING_CONFIG = `        release {
            // Populated only when CI (or a developer) passes the upload keystore
            // in as Gradle properties. Left empty otherwise so a machine without
            // the keystore can still configure the project.
            if (project.hasProperty("ZITCH_UPLOAD_STORE_FILE")) {
                storeFile file(project.property("ZITCH_UPLOAD_STORE_FILE"))
                storePassword project.property("ZITCH_UPLOAD_STORE_PASSWORD")
                keyAlias project.property("ZITCH_UPLOAD_KEY_ALIAS")
                keyPassword project.property("ZITCH_UPLOAD_KEY_PASSWORD")
            }
        }
`;

const BUILD_TYPE_SIGNING = `def zitchHasUploadKey = project.hasProperty("ZITCH_UPLOAD_STORE_FILE")
            println("[zitch] release signing: " + (zitchHasUploadKey ? "upload keystore" : "DEBUG key — sideload only, Play will reject this"))
            signingConfig zitchHasUploadKey ? signingConfigs.release : signingConfigs.debug`;

const DEBUG_SIGNING_LINE = 'signingConfig signingConfigs.debug';

/**
 * Rewrite app/build.gradle so the release build type picks its key at configure
 * time. Exported separately from the plugin so it can be unit-tested against the
 * real template text without running prebuild.
 *
 * @param {string} contents app/build.gradle
 * @returns {string}
 */
function addReleaseSigning(contents) {
  if (contents.includes(MARKER)) return contents; // already applied

  // 1. Point the release *build type* at the chosen config. Do this before
  //    inserting the signingConfigs entry, so the `release {` we search for is
  //    unambiguously the buildTypes one.
  const buildTypesAt = contents.indexOf('buildTypes {');
  if (buildTypesAt === -1) {
    throw new Error('withAndroidReleaseSigning: no `buildTypes {` in app/build.gradle');
  }
  const releaseAt = contents.indexOf('release {', buildTypesAt);
  if (releaseAt === -1) {
    throw new Error('withAndroidReleaseSigning: no `release {` build type in app/build.gradle');
  }
  const signingAt = contents.indexOf(DEBUG_SIGNING_LINE, releaseAt);
  if (signingAt === -1) {
    throw new Error(
      'withAndroidReleaseSigning: the release build type no longer reads ' +
        '`signingConfig signingConfigs.debug`. The template changed — re-check this plugin ' +
        'before trusting the signature of any build it produces.'
    );
  }
  contents =
    contents.slice(0, signingAt) +
    BUILD_TYPE_SIGNING +
    contents.slice(signingAt + DEBUG_SIGNING_LINE.length);

  // 2. Declare the `release` signing config next to `debug`.
  const configsAt = contents.indexOf('signingConfigs {');
  if (configsAt === -1) {
    throw new Error('withAndroidReleaseSigning: no `signingConfigs {` in app/build.gradle');
  }
  const insertAt = contents.indexOf('\n', configsAt) + 1;
  contents = contents.slice(0, insertAt) + SIGNING_CONFIG + contents.slice(insertAt);

  return contents;
}

const withAndroidReleaseSigning = (config) =>
  withAppBuildGradle(config, (cfg) => {
    if (cfg.modResults.language !== 'groovy') {
      throw new Error(
        'withAndroidReleaseSigning: app/build.gradle is not Groovy; cannot apply release signing'
      );
    }
    cfg.modResults.contents = addReleaseSigning(cfg.modResults.contents);
    return cfg;
  });

module.exports = withAndroidReleaseSigning;
module.exports.addReleaseSigning = addReleaseSigning;

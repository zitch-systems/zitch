// Expo config plugin: sign a sideload test build with every APK signature scheme.
//
// The app's minimum Android version is 7, so Gradle signs with scheme v2 only
// and leaves the classic (v1, JAR) signature out. Android itself accepts that,
// but an installer or security scanner that reads only the v1 signature sees
// an unsigned file. A test build is handed around as a file and must install
// on phones we have never seen, so it carries v1, v2 and v3 together.
//
// Applied only when ZITCH_ANDROID_VARIANT=test (the Android APK workflow). The
// store build is signed with the upload key by withAndroidReleaseSigning and is
// not touched here.

const { withAppBuildGradle } = require('expo/config-plugins');

const MARKER = 'enableV1Signing true';
const DEBUG_CONFIG = /(signingConfigs\s*\{\s*\n(?:[\s\S]*?\n)??\s*debug\s*\{\s*\n)/;
const SCHEMES = `            // Zitch test builds: every signature scheme, for the widest set of phones.
            enableV1Signing true
            enableV2Signing true
            enableV3Signing true
`;

/**
 * @param {string} contents app/build.gradle
 * @returns {string}
 */
function addAllSignatureSchemes(contents) {
  if (contents.includes(MARKER)) return contents; // already applied
  if (!DEBUG_CONFIG.test(contents)) {
    throw new Error('withAndroidTestSigning: no `signingConfigs { debug {` in app/build.gradle');
  }
  return contents.replace(DEBUG_CONFIG, `$1${SCHEMES}`);
}

const withAndroidTestSigning = (config) =>
  withAppBuildGradle(config, (cfg) => {
    if (process.env.ZITCH_ANDROID_VARIANT !== 'test') return cfg;
    if (cfg.modResults.language !== 'groovy') {
      throw new Error('withAndroidTestSigning: app/build.gradle is not Groovy');
    }
    cfg.modResults.contents = addAllSignatureSchemes(cfg.modResults.contents);
    return cfg;
  });

module.exports = withAndroidTestSigning;
module.exports.addAllSignatureSchemes = addAllSignatureSchemes;

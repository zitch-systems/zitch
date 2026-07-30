import { addReleaseSigning } from '../withAndroidReleaseSigning';

// Verbatim excerpt of expo-template-bare-minimum@54 android/app/build.gradle —
// the file `expo prebuild` generates. Both `signingConfig signingConfigs.debug`
// occurrences are kept, because telling them apart is the whole job: rewriting
// the wrong one would leave release builds debug-signed while looking fixed.
const TEMPLATE = `android {
    ndkVersion rootProject.ext.ndkVersion
    compileSdk rootProject.ext.compileSdkVersion
    defaultConfig {
        applicationId 'com.zitch.app'
        minSdkVersion rootProject.ext.minSdkVersion
        targetSdkVersion rootProject.ext.targetSdkVersion
        versionCode 11
        versionName "1.0.11"
    }
    signingConfigs {
        debug {
            storeFile file('debug.keystore')
            storePassword 'android'
            keyAlias 'androiddebugkey'
            keyPassword 'android'
        }
    }
    buildTypes {
        debug {
            signingConfig signingConfigs.debug
        }
        release {
            // Caution! In production, you need to generate your own keystore file.
            // see https://reactnative.dev/docs/signed-apk-android.
            signingConfig signingConfigs.debug
            shrinkResources false
            minifyEnabled enableMinifyInReleaseBuilds
        }
    }
}
`;

describe('addReleaseSigning', () => {
  const patched = addReleaseSigning(TEMPLATE);

  it('stops the release build type from hardcoding the debug key', () => {
    const releaseBlock = patched.slice(patched.indexOf('buildTypes {'));
    expect(releaseBlock).toContain(
      'signingConfig zitchHasUploadKey ? signingConfigs.release : signingConfigs.debug'
    );
  });

  it('leaves the debug build type signed with the debug key', () => {
    const buildTypes = patched.slice(patched.indexOf('buildTypes {'));
    const debugBlock = buildTypes.slice(
      buildTypes.indexOf('debug {'),
      buildTypes.indexOf('release {')
    );
    expect(debugBlock).toContain('signingConfig signingConfigs.debug');
    expect(debugBlock).not.toContain('zitchHasUploadKey');
  });

  it('declares a release signing config fed by the ZITCH_UPLOAD_* gradle properties', () => {
    const configs = patched.slice(
      patched.indexOf('signingConfigs {'),
      patched.indexOf('buildTypes {')
    );
    expect(configs).toContain('release {');
    expect(configs).toContain('project.hasProperty("ZITCH_UPLOAD_STORE_FILE")');
    expect(configs).toContain('storeFile file(project.property("ZITCH_UPLOAD_STORE_FILE"))');
    expect(configs).toContain('storePassword project.property("ZITCH_UPLOAD_STORE_PASSWORD")');
    expect(configs).toContain('keyAlias project.property("ZITCH_UPLOAD_KEY_ALIAS")');
    expect(configs).toContain('keyPassword project.property("ZITCH_UPLOAD_KEY_PASSWORD")');
    // The debug config must survive, or local debug builds stop working.
    expect(configs).toContain("keyAlias 'androiddebugkey'");
  });

  it('says in the build log which key signed the artifact', () => {
    expect(patched).toContain('[zitch] release signing: ');
    expect(patched).toContain('DEBUG key — sideload only, Play will reject this');
  });

  it('is idempotent — prebuild may apply mods more than once', () => {
    expect(addReleaseSigning(patched)).toBe(patched);
  });

  it('refuses to guess when the template stops matching', () => {
    // If Expo restructures the release block, silently doing nothing would ship a
    // debug-signed AAB. Fail the build instead.
    const drifted = TEMPLATE.replace(
      /signingConfig signingConfigs\.debug\n            shrinkResources/,
      'signingConfig signingConfigs.somethingElse\n            shrinkResources'
    );
    expect(() => addReleaseSigning(drifted)).toThrow(/no longer reads/);

    expect(() => addReleaseSigning('android {\n}\n')).toThrow(/buildTypes/);
  });
});

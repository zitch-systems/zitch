import { addReleaseSigning } from '../withAndroidReleaseSigning';
import { addAllSignatureSchemes } from '../withAndroidTestSigning';

// Excerpt of the Expo SDK 54 template's android/app/build.gradle.
const TEMPLATE = `android {
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
            signingConfig signingConfigs.debug
        }
    }
}
`;

describe('addAllSignatureSchemes', () => {
  const patched = addAllSignatureSchemes(TEMPLATE);
  const debugConfig = (text: string) => {
    const configs = text.slice(text.indexOf('signingConfigs {'), text.indexOf('buildTypes {'));
    return configs.slice(configs.indexOf('debug {'));
  };

  it('turns on v1, v2 and v3 for the debug signing config', () => {
    for (const scheme of ['enableV1Signing true', 'enableV2Signing true', 'enableV3Signing true']) {
      expect(debugConfig(patched)).toContain(scheme);
    }
    expect(debugConfig(patched)).toContain("storeFile file('debug.keystore')");
  });

  it('leaves the build types alone', () => {
    expect(patched.slice(patched.indexOf('buildTypes {'))).toBe(TEMPLATE.slice(TEMPLATE.indexOf('buildTypes {')));
  });

  it('is idempotent', () => {
    expect(addAllSignatureSchemes(patched)).toBe(patched);
  });

  it('never touches the upload-key config, in either plugin order', () => {
    const both = addAllSignatureSchemes(addReleaseSigning(TEMPLATE));
    expect(both).toBe(addReleaseSigning(addAllSignatureSchemes(TEMPLATE)));
    const configs = both.slice(both.indexOf('signingConfigs {'), both.indexOf('buildTypes {'));
    const release = configs.slice(configs.indexOf('release {'), configs.indexOf('debug {'));
    expect(release).not.toContain('enableV1Signing');
    expect(debugConfig(both)).toContain('enableV1Signing true');
  });

  it('fails loudly when the template has no debug signing config', () => {
    expect(() => addAllSignatureSchemes('android {\n}\n')).toThrow(/signingConfigs/);
  });
});

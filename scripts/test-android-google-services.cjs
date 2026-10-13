const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const { resolveGoogleServices, validateGoogleServices } = require('./android-google-services.cjs');

const document = () => ({
  project_info: { project_number: '123456', project_id: 'test-project' },
  client: [{
    client_info: { mobilesdk_app_id: '1:123456:android:abc123', android_client_info: { package_name: 'com.zitch.app' } },
    api_key: [{ current_key: 'test-fixture-not-a-secret' }],
  }],
});

test('requires a complete Android client for this exact package and Firebase project', () => {
  assert.deepEqual(validateGoogleServices(document(), 'com.zitch.app'), document());
  assert.throws(() => validateGoogleServices(document(), 'com.some.other.app'), /does not match/);
  for (const invalid of [null, {}, { client: 'invalid' }, { ...document(), project_info: { project_number: '999', project_id: 'wrong-project' } }]) {
    assert.throws(() => validateGoogleServices(invalid, 'com.zitch.app'), /incomplete|does not match/);
  }
});

test('preserves internal preview builds but fails production without configuration', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-firebase-test-'));
  try {
    assert.equal(resolveGoogleServices('com.zitch.app', {}, root), undefined);
    assert.throws(() => resolveGoogleServices('com.zitch.app', { ZITCH_REQUIRE_PUSH_CONFIG: 'true' }, root), /push requires/);
    assert.throws(() => resolveGoogleServices('com.zitch.app', { GOOGLE_SERVICES_JSON: 'missing.json' }, root), /push requires/);
    const filename = path.join(root, 'google-services.json');
    fs.writeFileSync(filename, JSON.stringify(document()));
    assert.equal(resolveGoogleServices('com.zitch.app', { ZITCH_REQUIRE_PUSH_CONFIG: 'true' }, root), filename);
    fs.writeFileSync(filename, 'invalid json');
    assert.throws(() => resolveGoogleServices('com.zitch.app', {}, root), /readable JSON/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('publishes only the native push capability and preserves the existing Expo project', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'app.config.js'), 'utf8');
  for (const filename of [undefined, '/private-build/google-services.json']) {
    const context = { module: { exports: {} }, require: (name) => {
      assert.equal(name, './scripts/android-google-services.cjs');
      return { resolveGoogleServices: (packageName) => {
        assert.equal(packageName, 'com.zitch.app');
        return filename;
      } };
    } };
    vm.runInNewContext(source, context);
    const result = context.module.exports({ config: {
      android: { package: 'com.zitch.app' }, extra: { eas: { projectId: 'existing-project' } },
    } });
    assert.equal(result.extra.push.androidConfigured, Boolean(filename));
    assert.equal(result.extra.eas.projectId, 'existing-project');
    assert.equal(result.android.googleServicesFile, filename);
    assert.deepEqual(Object.keys(result.extra.push), ['androidConfigured']);
  }
});

test('EAS production requires Android push configuration without applying the Android gate to iOS', () => {
  const eas = require('../eas.json');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zitch-production-config-test-'));
  try {
    const production = eas.build.production;
    const androidEnv = { ...production.env, ...production.android.env };
    assert.equal(androidEnv.ZITCH_REQUIRE_PUSH_CONFIG, 'true');
    assert.throws(() => resolveGoogleServices('com.zitch.app', androidEnv, root), /push requires/);
    assert.equal(production.env.ZITCH_REQUIRE_PUSH_CONFIG, undefined);
    assert.equal(production.ios?.env?.ZITCH_REQUIRE_PUSH_CONFIG, undefined);
    assert.equal(eas.build.preview.android.env?.ZITCH_REQUIRE_PUSH_CONFIG, undefined);
    fs.writeFileSync(path.join(root, 'google-services.json'), JSON.stringify(document()));
    assert.equal(resolveGoogleServices('com.zitch.app', androidEnv, root), path.join(root, 'google-services.json'));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

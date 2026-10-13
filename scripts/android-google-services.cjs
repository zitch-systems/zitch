const fs = require('node:fs');
const path = require('node:path');

function validateGoogleServices(document, packageName) {
  const project = document?.project_info;
  const client = Array.isArray(document?.client)
    ? document.client.find((item) => item?.client_info?.android_client_info?.package_name === packageName) : null;
  if (!project?.project_id || !/^\d+$/.test(String(project?.project_number || ''))
      || typeof client?.client_info?.mobilesdk_app_id !== 'string'
      || !client.client_info.mobilesdk_app_id.startsWith(`1:${project.project_number}:android:`)
      || !Array.isArray(client?.api_key)
      || !client.api_key.some((key) => typeof key?.current_key === 'string' && key.current_key.length > 0)) {
    throw new Error(`Firebase Android configuration is incomplete or does not match ${packageName}.`);
  }
  return document;
}

function resolveGoogleServices(packageName, env = process.env, root = path.resolve(__dirname, '..')) {
  const configured = env.GOOGLE_SERVICES_JSON;
  const filename = path.resolve(root, configured || 'google-services.json');
  if (!fs.existsSync(filename)) {
    if (configured || env.ZITCH_REQUIRE_PUSH_CONFIG === 'true') {
      throw new Error('Android push requires google-services.json. Configure ANDROID_GOOGLE_SERVICES_JSON_BASE64 or GOOGLE_SERVICES_JSON before building.');
    }
    return undefined;
  }
  let document;
  try { document = JSON.parse(fs.readFileSync(filename, 'utf8')); }
  catch { throw new Error('Firebase Android configuration must be a readable JSON file.'); }
  validateGoogleServices(document, packageName);
  return filename;
}

if (require.main === module) {
  try {
    const root = path.resolve(__dirname, '..');
    const packageName = require('../app.json').expo.android.package;
    const encoded = process.env.ANDROID_GOOGLE_SERVICES_JSON_BASE64;
    if (encoded) {
      let document;
      try { document = JSON.parse(Buffer.from(encoded, 'base64').toString('utf8')); }
      catch { throw new Error('ANDROID_GOOGLE_SERVICES_JSON_BASE64 must contain base64-encoded Firebase Android JSON.'); }
      validateGoogleServices(document, packageName);
      fs.writeFileSync(path.join(root, 'google-services.json'), JSON.stringify(document), { mode: 0o600 });
    }
    const filename = resolveGoogleServices(packageName);
    console.log(filename
      ? 'Firebase Android configuration validated. Live push delivery still requires the matching FCM v1 credential in Expo.'
      : 'Firebase Android configuration absent: this internal test build cannot receive remote push notifications.');
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

module.exports = { resolveGoogleServices, validateGoogleServices };

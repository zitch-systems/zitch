import { applyAndroidSecurity } from '../withAndroidSecurity';

describe('applyAndroidSecurity', () => {
  it('disables cleartext traffic and Android backup', () => {
    const manifest = {
      manifest: {
        application: [{ $: { 'android:allowBackup': 'true' } }],
        'uses-permission': [
          { $: { 'android:name': 'android.permission.INTERNET' } },
          { $: { 'android:name': 'android.permission.SYSTEM_ALERT_WINDOW' } },
        ],
      },
    };

    expect(applyAndroidSecurity(manifest)).toBe(manifest);
    expect(manifest.manifest.application[0].$).toMatchObject({
      'android:allowBackup': 'false',
      'android:usesCleartextTraffic': 'false',
    });
    expect(manifest.manifest['uses-permission']).toEqual([
      { $: { 'android:name': 'android.permission.INTERNET' } },
      {
        $: {
          'android:name': 'android.permission.SYSTEM_ALERT_WINDOW',
          'tools:node': 'remove',
        },
      },
    ]);
  });

  it('refuses to silently succeed when the manifest has no application', () => {
    expect(() => applyAndroidSecurity({ manifest: {} })).toThrow(/no <application>/);
  });
});

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

  it('keeps the activity running when a foldable opens or closes', () => {
    const manifest = {
      manifest: {
        application: [{ $: {}, activity: [{ $: { 'android:name': '.MainActivity', 'android:configChanges': 'keyboard|orientation|screenSize' } }] }],
      },
    };
    applyAndroidSecurity(manifest);
    const changes = manifest.manifest.application[0].activity[0].$['android:configChanges'].split('|');
    for (const change of ['screenSize', 'smallestScreenSize', 'screenLayout', 'orientation']) expect(changes).toContain(change);
    expect(new Set(changes).size).toBe(changes.length);
  });

  it('refuses to silently succeed when the manifest has no application', () => {
    expect(() => applyAndroidSecurity({ manifest: {} })).toThrow(/no <application>/);
  });
});

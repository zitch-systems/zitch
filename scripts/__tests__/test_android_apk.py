import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile


spec = importlib.util.spec_from_file_location('apk_verifier', Path(__file__).parents[1] / 'verify-android-apk.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
CONFIG = {'version': '1.0.4', 'android': {'package': 'com.zitch.app', 'versionCode': 12,
          'blockedPermissions': ['android.permission.RECORD_AUDIO']}}
BADGING = "package: name='com.zitch.app' versionCode='12' versionName='1.0.4'\nuses-permission: name='android.permission.INTERNET'\n"
MANIFEST = "A: android:allowBackup(0x01010280)=(type 0x12)0x0\nA: android:usesCleartextTraffic(0x010104ec)=(type 0x12)0x0\n"
FINGERPRINT = 'a' * 64
SIGNER = f'Signer #1 certificate DN: C=US, O=Android, CN=Android Debug\nSigner #1 certificate SHA-256 digest: {FINGERPRINT}\n'


class AndroidApkVerificationTests(unittest.TestCase):
    def test_valid_native_policy(self):
        self.assertEqual(verifier.verify_manifest(BADGING, MANIFEST, CONFIG), ['android.permission.INTERNET'])

    def test_wrong_package_or_version_fails(self):
        for badging in (BADGING.replace('com.zitch.app', 'com.other.app'), BADGING.replace("versionCode='12'", "versionCode='11'")):
            with self.assertRaisesRegex(ValueError, 'package/version'):
                verifier.verify_manifest(badging, MANIFEST, CONFIG)

    def test_unsafe_permissions_or_debuggable_binary_fail(self):
        for suffix in ("uses-permission: name='android.permission.RECORD_AUDIO'", 'application-debuggable'):
            with self.assertRaises(ValueError):
                verifier.verify_manifest(BADGING + suffix, MANIFEST, CONFIG)

    def test_manifest_policy_must_be_explicit(self):
        for manifest in ('', MANIFEST.replace('=(type 0x12)0x0', '=(type 0x12)0xffffffff', 1)):
            with self.assertRaisesRegex(ValueError, 'explicitly disable'):
                verifier.verify_manifest(BADGING, manifest, CONFIG)

    def test_wrong_signer_and_debug_production_key_fail(self):
        self.assertEqual(verifier.verify_signer(SIGNER, FINGERPRINT, 'preview'), FINGERPRINT)
        with self.assertRaisesRegex(ValueError, 'match'):
            verifier.verify_signer(SIGNER, 'b' * 64, 'preview')
        with self.assertRaisesRegex(ValueError, 'Debug'):
            verifier.verify_signer(SIGNER, FINGERPRINT, 'upload')

    def test_unsigned_or_multiple_signers_fail(self):
        for signer in ('', SIGNER + SIGNER.replace('#1', '#2')):
            with self.assertRaisesRegex(ValueError, 'match'):
                verifier.verify_signer(signer, FINGERPRINT, 'preview')

    def test_apk_requires_embedded_bundle_and_device_libraries(self):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / 'app.apk'
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('assets/index.android.bundle', 'bundle')
                archive.writestr('lib/arm64-v8a/libhermes.so', 'arm64')
                archive.writestr('lib/x86_64/libhermes.so', 'x86_64')
            verifier.verify_payload(apk)
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('lib/arm64-v8a/libhermes.so', 'arm64')
            with self.assertRaisesRegex(ValueError, 'JavaScript'):
                verifier.verify_payload(apk)


if __name__ == '__main__':
    unittest.main()

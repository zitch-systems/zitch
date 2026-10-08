import importlib.util
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
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
SIGNER = f'Number of signers: 1\nSigner #1 certificate DN: C=US, O=Android, CN=Android Debug\nSigner #1 certificate SHA-256 digest: {FINGERPRINT}\n'


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
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                verifier.verify_signer(signer, FINGERPRINT, 'preview')

    def test_sdk37_scheme_labels_and_sdk_ranges_keep_exact_certificate_check(self):
        for label in ('V1 Signer:', 'V2 Signer:', 'V3.1 Signer:',
                      'Signer (minSdkVersion=33, maxSdkVersion=2147483647)'):
            with self.subTest(label=label):
                output = SIGNER.replace('Signer #1', label)
                self.assertEqual(verifier.verify_signer(output, FINGERPRINT, 'preview'), FINGERPRINT)
        repeated = SIGNER + SIGNER.replace('Number of signers: 1\n', '').replace('Signer #1', 'V2 Signer:')
        self.assertEqual(verifier.verify_signer(repeated, FINGERPRINT, 'preview'), FINGERPRINT)
        with self.assertRaisesRegex(ValueError, 'match'):
            verifier.verify_signer(repeated.replace(f'V2 Signer: certificate SHA-256 digest: {FINGERPRINT}',
                                                  f'V2 Signer: certificate SHA-256 digest: {"b" * 64}'), FINGERPRINT, 'preview')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            verifier.verify_signer(repeated.replace('Number of signers: 1', 'Number of signers: 2'), FINGERPRINT, 'preview')
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            verifier.verify_signer(SIGNER.replace('Signer #1', 'Unknown Signer'), FINGERPRINT, 'preview')

    @unittest.skipUnless(shutil.which('keytool'), 'A JDK is required')
    def test_real_apksigner_output_and_keystore_certificate(self):
        try:
            apksigner = verifier.sdk_tool('apksigner')
        except ValueError as error:
            self.skipTest(str(error))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store, apk = path / 'test.jks', path / 'test.apk'
            env = {**os.environ, 'ZITCH_TEST_STORE_PASSWORD': 'test-only-password'}
            subprocess.run([
                'keytool', '-genkeypair', '-keystore', str(store), '-alias', 'test-debug',
                '-storepass:env', 'ZITCH_TEST_STORE_PASSWORD', '-keypass:env', 'ZITCH_TEST_STORE_PASSWORD',
                '-dname', 'CN=Android Debug,O=Zitch Verification Test', '-keyalg', 'RSA', '-validity', '30',
            ], check=True, capture_output=True, env=env)
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('assets/fixture.txt', 'APK signature parser regression')
            subprocess.run([
                apksigner, 'sign', '--min-sdk-version', '23', '--ks', str(store),
                '--ks-key-alias', 'test-debug', '--ks-pass', 'env:ZITCH_TEST_STORE_PASSWORD', str(apk),
            ], check=True, capture_output=True, env=env)
            certificate = subprocess.check_output([
                'keytool', '-exportcert', '-keystore', str(store), '-alias', 'test-debug',
                '-storepass:env', 'ZITCH_TEST_STORE_PASSWORD',
            ], env=env)
            fingerprint = hashlib.sha256(certificate).hexdigest()
            # Explicit 23–25 allows a tiny ZIP fixture without a compiled Android
            # manifest; production verification still checks the APK's full range.
            command = [apksigner, 'verify', '--min-sdk-version', '23', '--max-sdk-version', '25',
                       '--verbose', '--print-certs', str(apk)]
            output = subprocess.check_output(command, text=True)
            self.assertEqual(verifier.verify_signer(output, fingerprint, 'preview'), fingerprint)
            with self.assertRaisesRegex(ValueError, 'match'):
                verifier.verify_signer(output, '0' * 64, 'preview')
            with zipfile.ZipFile(apk, 'a') as archive:
                archive.writestr('assets/unsigned.txt', 'Added after signing')
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

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

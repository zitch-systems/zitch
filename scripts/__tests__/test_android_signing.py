"""Exercise real JDK signing, including unsigned files appended to an AAB."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile


@unittest.skipUnless(all(shutil.which(command) for command in ('keytool', 'jarsigner', 'openssl')), 'A full JDK and OpenSSL are required')
class AndroidSigningTests(unittest.TestCase):
    def test_correct_self_signed_upload_key_passes_but_unsigned_payload_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = path / 'test.jks'
            archive = path / 'test.aab'
            env = {**os.environ, 'ZITCH_UPLOAD_STORE_PASSWORD': 'test-only-password',
                   'ZITCH_UPLOAD_KEY_ALIAS': 'test-release'}
            subprocess.run([
                'keytool', '-genkeypair', '-keystore', str(store), '-alias', 'test-release',
                '-storepass:env', 'ZITCH_UPLOAD_STORE_PASSWORD', '-keypass:env', 'ZITCH_UPLOAD_STORE_PASSWORD',
                '-dname', 'CN=Zitch Verification Test', '-keyalg', 'RSA', '-validity', '30',
            ], env=env, check=True, capture_output=True)
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('base/manifest/test.xml', '<manifest />')
            subprocess.run([
                'jarsigner', '-keystore', str(store), '-storepass:env', 'ZITCH_UPLOAD_STORE_PASSWORD',
                str(archive), 'test-release',
            ], env=env, check=True, capture_output=True)
            script = Path(__file__).parents[1] / 'verify-android-signing.sh'
            accepted = subprocess.run(['bash', str(script), str(archive), str(store)], env=env, capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            with zipfile.ZipFile(archive, 'a') as output:
                output.writestr('base/dex/unsigned.txt', 'This entry was added after signing.')
            rejected = subprocess.run(['bash', str(script), str(archive), str(store)], env=env, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0, 'Unsigned appended payload must not verify')


if __name__ == '__main__':
    unittest.main()

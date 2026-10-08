"""Replace only the JavaScript bundle for input diagnostics; never publish this APK."""
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile

source = Path('release-apk/Zitch-Android-Test.apk')
assert hashlib.sha256(source.read_bytes()).hexdigest() == '9f07216d988f1eec571916e731385b14e91f7351563234f9001112efd3b50690'
bundle = Path('diagnostic.hbc').read_bytes()
with zipfile.ZipFile(source) as original, zipfile.ZipFile('diagnostic-unsigned.apk', 'w') as target:
    assert 'assets/index.android.bundle' in original.namelist()
    for entry in original.infolist():
        if entry.filename == 'META-INF/MANIFEST.MF' or (entry.filename.startswith('META-INF/') and entry.filename.upper().endswith(('.SF', '.RSA', '.DSA', '.EC'))):
            continue
        target.writestr(entry, bundle if entry.filename == 'assets/index.android.bundle' else original.read(entry))
sdk = Path(os.environ.get('ANDROID_HOME') or os.environ['ANDROID_SDK_ROOT'])
versions = sorted(sdk.joinpath('build-tools').glob('*'), key=lambda path: tuple(int(p) for p in path.name.split('.') if p.isdigit()))
build_tools = next(path for path in reversed(versions) if (path / 'apksigner').exists() and (path / 'zipalign').exists())
subprocess.run([str(build_tools / 'zipalign'), '-f', '-p', '4', 'diagnostic-unsigned.apk', 'diagnostic-aligned.apk'], check=True)
subprocess.run([str(build_tools / 'apksigner'), 'sign', '--ks', 'android/app/debug.keystore', '--ks-key-alias', 'androiddebugkey', '--ks-pass', 'pass:android', '--key-pass', 'pass:android', '--out', 'diagnostic-only.apk', 'diagnostic-aligned.apk'], check=True)
subprocess.run([str(build_tools / 'apksigner'), 'verify', '--verbose', 'diagnostic-only.apk'], check=True)
print('Diagnostic-only APK created. It is not a release candidate and must not be published.')

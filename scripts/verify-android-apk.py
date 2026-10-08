#!/usr/bin/env python3
"""Verify the actual APK identity, signature, native policy and bundled runtime."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile


def sdk_tool(name):
    installed = shutil.which(name)
    if installed:
        return installed
    sdk = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if sdk:
        candidates = sorted(Path(sdk).glob(f'build-tools/*/{name}'), reverse=True)
        if candidates:
            return str(candidates[0])
    raise ValueError(f'Android SDK tool is missing: {name}')


def verify_manifest(badging, xmltree, expo):
    match = re.search(r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'", badging, re.M)
    expected = (expo['android']['package'], str(expo['android']['versionCode']), expo['version'])
    if not match or match.groups() != expected:
        raise ValueError('APK package/version does not match app.json')
    if 'application-debuggable' in badging or re.search(
        r'android:debuggable\([^)]*\)=\(type 0x12\)0xffffffff', xmltree
    ):
        raise ValueError('APK is debuggable; a release-mode binary is required')
    for attribute in ('allowBackup', 'usesCleartextTraffic'):
        if not re.search(rf'android:{attribute}\([^)]*\)=\(type 0x12\)0x0\b', xmltree):
            raise ValueError(f'APK must explicitly disable android:{attribute}')
    permissions = re.findall(r"^uses-permission(?:-sdk-\d+)?: name='([^']+)'", badging, re.M)
    forbidden = set(expo['android'].get('blockedPermissions', []))
    present = forbidden.intersection(permissions)
    if present:
        raise ValueError(f'APK contains blocked permissions: {sorted(present)}')
    return sorted(set(permissions))


def verify_payload(apk):
    with zipfile.ZipFile(apk) as archive:
        files = archive.namelist()
        bundle = 'assets/index.android.bundle'
        if bundle not in files or archive.getinfo(bundle).file_size == 0:
            raise ValueError('APK has no bundled JavaScript; it would depend on Metro')
        if not any(name.startswith('lib/arm64-v8a/') for name in files):
            raise ValueError('APK is missing arm64 support for current Android phones')
        if not any(name.startswith('lib/x86_64/') for name in files):
            raise ValueError('APK is missing x86_64 support for emulator verification')


def verify_signer(output, expected_fingerprint, signing):
    fingerprints = re.findall(r'^Signer #\d+ certificate SHA-256 digest: ([0-9a-fA-F]+)$', output, re.M)
    if len(fingerprints) != 1 or fingerprints[0].lower() != expected_fingerprint.lower():
        raise ValueError('APK signer does not match the configured keystore')
    debug_certificate = bool(re.search(r'^Signer #\d+ certificate DN: .*CN=Android Debug(?:,|$)', output, re.M))
    if signing == 'upload' and debug_certificate:
        raise ValueError('Production APK must not use an Android Debug certificate')
    if signing == 'preview' and not debug_certificate:
        raise ValueError('Preview APK was not signed with the expected Android Debug key')
    return fingerprints[0].lower()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apk', required=True, type=Path)
    parser.add_argument('--config', default='app.json', type=Path)
    parser.add_argument('--keystore', required=True, type=Path)
    parser.add_argument('--signing', required=True, choices=('preview', 'upload'))
    parser.add_argument('--revision', required=True)
    parser.add_argument('--metadata', required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.revision):
        raise ValueError('A full source commit SHA is required')
    expo = json.loads(args.config.read_text())['expo']
    for filename in (args.apk, args.keystore):
        if not filename.is_file() or filename.stat().st_size == 0:
            raise ValueError(f'Missing or empty build input: {filename}')

    aapt = sdk_tool('aapt')
    badging = subprocess.check_output([aapt, 'dump', 'badging', str(args.apk)], text=True)
    manifest = subprocess.check_output([aapt, 'dump', 'xmltree', str(args.apk), 'AndroidManifest.xml'], text=True)
    permissions = verify_manifest(badging, manifest, expo)
    verify_payload(args.apk)

    signer = subprocess.check_output([sdk_tool('apksigner'), 'verify', '--verbose', '--print-certs', str(args.apk)], text=True)
    env = os.environ.copy()
    if args.signing == 'preview':
        env['ZITCH_VERIFY_STORE_PASSWORD'] = 'android'
        alias = 'androiddebugkey'
    else:
        env['ZITCH_VERIFY_STORE_PASSWORD'] = env.get('ZITCH_UPLOAD_STORE_PASSWORD', '')
        alias = env.get('ZITCH_UPLOAD_KEY_ALIAS', '')
        if not env['ZITCH_VERIFY_STORE_PASSWORD'] or not alias:
            raise ValueError('Production signing verification requires upload-key credentials')
    certificate = subprocess.check_output([
        'keytool', '-exportcert', '-keystore', str(args.keystore),
        '-storepass:env', 'ZITCH_VERIFY_STORE_PASSWORD', '-alias', alias,
    ], env=env)
    fingerprint = verify_signer(signer, hashlib.sha256(certificate).hexdigest(), args.signing)
    digest = hashlib.sha256()
    with args.apk.open('rb') as apk_file:
        for chunk in iter(lambda: apk_file.read(1024 * 1024), b''):
            digest.update(chunk)
    metadata = {
        'package': expo['android']['package'],
        'version': expo['version'],
        'version_code': expo['android']['versionCode'],
        'source_revision': args.revision,
        'signing': 'internal-test-debug-key' if args.signing == 'preview' else 'upload-key',
        'signing_certificate_sha256': fingerprint,
        'apk_sha256': digest.hexdigest(),
        'permissions': permissions,
        'release_mode': True,
        'bundled_javascript': True,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(metadata, indent=2) + '\n')
    print(f"Verified {args.apk.name}: {metadata['version']} ({metadata['version_code']}), {metadata['signing']}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f'Android APK verification failed: {error}', file=sys.stderr)
        sys.exit(1)

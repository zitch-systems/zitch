#!/usr/bin/env python3
"""Exercise the installed release APK without credentials or network submissions.

Run only on a disposable emulator: this clears the package's local app data.
Captures UI dumps, screenshots and logcat for the release evidence artifact.
"""
import argparse
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--package', default='com.zitch.app')
parser.add_argument('--output-dir', required=True)
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', args.package):
    parser.error('Invalid Android package')
out = Path(args.output_dir)
out.mkdir(parents=True, exist_ok=True)


def adb(*parts, binary=False, check=True):
    return subprocess.run(['adb', *parts], check=check, capture_output=True,
                          text=not binary, timeout=30).stdout


def hierarchy():
    adb('shell', 'uiautomator', 'dump', '/sdcard/zitch-smoke.xml')
    xml = adb('shell', 'cat', '/sdcard/zitch-smoke.xml')
    return xml, ET.fromstring(xml)


def nodes_with(root, text):
    return [n for n in root.iter('node') if text in (n.get('text', ''), n.get('content-desc', ''))]


def wait_for(text, seconds=45):
    deadline = time.monotonic() + seconds
    last = ''
    while time.monotonic() < deadline:
        try:
            last, root = hierarchy()
            if nodes_with(root, text):
                return root
        except (subprocess.SubprocessError, ET.ParseError) as exc:
            last = str(exc)
        time.sleep(0.75)
    (out / 'failed-ui.txt').write_text(last)
    raise AssertionError(f'Expected visible text: {text}')


def capture(name):
    xml, _ = hierarchy()
    (out / f'{name}.xml').write_text(xml)
    (out / f'{name}.png').write_bytes(adb('exec-out', 'screencap', '-p', binary=True))


def tap(text):
    root = wait_for(text)
    matches = nodes_with(root, text)
    # Text may be nested in an accessible button; tapping its centre still
    # exercises the same Android touch dispatch as a user.
    bounds = matches[0].get('bounds', '')
    values = [int(v) for v in re.findall(r'\d+', bounds)]
    if len(values) != 4:
        raise AssertionError(f'No tappable bounds for {text}: {bounds}')
    left, top, right, bottom = values
    adb('shell', 'input', 'tap', str((left + right) // 2), str((top + bottom) // 2))


try:
    # Never run clear-data against a person's physical device.
    if adb('shell', 'getprop', 'ro.kernel.qemu').strip() != '1':
        raise RuntimeError('Release smoke requires a disposable Android emulator')
    adb('logcat', '-c')
    adb('shell', 'pm', 'clear', args.package)
    adb('shell', 'monkey', '-p', args.package, '-c', 'android.intent.category.LAUNCHER', '1')
    wait_for('Skip', seconds=60)
    capture('01-onboarding')
    tap('Skip')
    wait_for('Welcome back')
    wait_for('Email or phone')
    wait_for('Password')
    wait_for('Forgot password?')
    capture('02-signin')
    tap('Sign in')
    wait_for('Email or phone cannot be empty')
    capture('03-local-validation')
    tap('Close notification')
    wait_for('Welcome back')
    # Reopen from background to cover the lock/lifecycle handoff on an
    # unauthenticated install. No account is created and no OTP is sent.
    adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
    adb('shell', 'monkey', '-p', args.package, '-c', 'android.intent.category.LAUNCHER', '1')
    wait_for('Welcome back')
    capture('04-resume')
    pid = adb('shell', 'pidof', args.package).strip().split()[0]
    log = adb('logcat', '-d', '--pid', pid, '-v', 'threadtime')
    if re.search(r'FATAL EXCEPTION|Fatal signal|ReactNativeJS:.*(?:TypeError|ReferenceError)', log):
        raise AssertionError('Native/JavaScript crash detected in emulator log')
    (out / 'result.txt').write_text('PASS: release APK installed, cold launch, onboarding, sign-in, empty-field validation, background/resume. No credentials or financial actions.\n')
    print((out / 'result.txt').read_text())
finally:
    (out / 'logcat.txt').write_text(adb('logcat', '-d', '-v', 'threadtime', check=False))

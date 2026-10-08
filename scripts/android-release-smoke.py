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

# Initialized only by main(), so helpers can be regression-tested without ADB.
out: Path


def adb(*parts, binary=False, check=True):
    return subprocess.run(['adb', *parts], check=check, capture_output=True,
                          text=not binary, timeout=30).stdout


def hierarchy():
    adb('shell', 'uiautomator', 'dump', '/sdcard/zitch-smoke.xml')
    xml = adb('shell', 'cat', '/sdcard/zitch-smoke.xml')
    return xml, ET.fromstring(xml)


def nodes_with(root, text, attributes=None):
    return [n for n in root.iter('node')
            if text in (n.get('text', ''), n.get('content-desc', ''))
            and all(n.get(key) == value for key, value in (attributes or {}).items())]


def wait_for(text, seconds=45, attributes=None):
    deadline = time.monotonic() + seconds
    last = ''
    while time.monotonic() < deadline:
        try:
            last, root = hierarchy()
            if nodes_with(root, text, attributes):
                return root
        except (subprocess.SubprocessError, ET.ParseError) as exc:
            last = str(exc)
        time.sleep(0.75)
    (out / 'failed-ui.txt').write_text(last)
    raise AssertionError(f'Expected visible control: {text}, attributes={attributes}')


def capture(name):
    xml, _ = hierarchy()
    (out / f'{name}.xml').write_text(xml)
    (out / f'{name}.png').write_bytes(adb('exec-out', 'screencap', '-p', binary=True))


def tap(text, attributes=None):
    root = wait_for(text, attributes=attributes)
    matches = nodes_with(root, text, attributes)
    # Prefer the accessible button/field when a nested label has the same text.
    # Explicit EditText selection is essential: its separate label cannot focus it.
    matches.sort(key=lambda node: node.get('clickable') != 'true')
    node = matches[0]
    if node.get('enabled') != 'true':
        raise AssertionError(f'Control disabled: {text}')
    bounds = node.get('bounds', '')
    values = [int(v) for v in re.findall(r'-?\d+', bounds)]
    if len(values) != 4:
        raise AssertionError(f'No tappable bounds for {text}: {bounds}')
    left, top, right, bottom = values
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise AssertionError(f'Control has no visible area: {text}: {bounds}')
    adb('shell', 'input', 'tap', str((left + right) // 2), str((top + bottom) // 2))


def assert_empty_signin(root):
    # Android exposes a blank TextInput's placeholder as its text in UIAutomator.
    # Refuse even this validation-only submission if credentials have appeared.
    placeholders = {'Email or phone': 'Email or phone number', 'Password': 'Enter password'}
    for label, placeholder in placeholders.items():
        fields = nodes_with(root, label, {'class': 'android.widget.EditText'})
        if len(fields) != 1 or fields[0].get('text', '') not in ('', placeholder):
            raise AssertionError(f'Refusing to submit a nonempty or unknown {label} field')


def keyboard_shown(dump):
    # API 34 InputMethodManagerService reports mInputShown in its dumpsys output.
    # An unknown/missing value must not be mistaken for a dismissed keyboard.
    match = re.search(r'\bmInputShown=(true|false)\b', dump)
    return match.group(1) == 'true' if match else None


def wait_for_keyboard(shown, seconds=15):
    deadline = time.monotonic() + seconds
    last = ''
    while time.monotonic() < deadline:
        last = adb('shell', 'dumpsys', 'input_method')
        if keyboard_shown(last) is shown:
            (out / f'keyboard-{"shown" if shown else "hidden"}.txt').write_text(last)
            return
        time.sleep(0.5)
    (out / 'failed-keyboard.txt').write_text(last)
    raise AssertionError(f'Expected soft keyboard shown={shown}')


def run_smoke(package):
    # Never run clear-data against a person's physical device.
    if adb('shell', 'getprop', 'ro.kernel.qemu').strip() != '1':
        raise RuntimeError('Release smoke requires a disposable Android emulator')
    adb('logcat', '-c')
    if adb('shell', 'pm', 'clear', package).strip() != 'Success':
        raise RuntimeError('Could not clear the installed test app')
    # Ensure native keyboard behavior is tested even with the emulator's hardware
    # keyboard attached. This setting is only changed after the emulator guard.
    adb('shell', 'settings', 'put', 'secure', 'show_ime_with_hard_keyboard', '1')
    adb('shell', 'monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1')
    wait_for('Skip', seconds=60)
    capture('01-onboarding')
    tap('Skip')
    wait_for('Welcome back')
    wait_for('Email or phone')
    wait_for('Password')
    wait_for('Forgot password?')
    capture('02-signin')

    # Require live JavaScript state updates, not merely a rendered sign-in screen.
    tap('Show password')
    wait_for('Hide password')
    wait_for('Password', attributes={'class': 'android.widget.EditText', 'password': 'false'})
    capture('03-password-visible')
    tap('Hide password')
    wait_for('Show password')
    wait_for('Password', attributes={'class': 'android.widget.EditText', 'password': 'true'})

    tap('Email or phone', attributes={'class': 'android.widget.EditText'})
    wait_for('Email or phone', attributes={'class': 'android.widget.EditText', 'focused': 'true'})
    wait_for_keyboard(True)
    capture('04-native-input-focus')
    adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
    wait_for_keyboard(False)
    wait_for('Welcome back')
    capture('05-keyboard-dismissed')

    _, root = hierarchy()
    assert_empty_signin(root)
    tap('Sign in')
    wait_for('Email or phone cannot be empty')
    capture('06-local-validation')
    tap('Close notification')
    wait_for('Welcome back')

    # Only open and leave recovery. Never request a reset code or enter an account.
    tap('Forgot password?')
    wait_for('Reset password')
    wait_for('Send reset code', attributes={'enabled': 'false'})
    capture('07-password-recovery')
    tap('Go back')
    wait_for('Welcome back')
    wait_for('Forgot password?')
    capture('08-recovery-return')

    # Reopen from background to cover the lock/lifecycle handoff on an
    # unauthenticated install. No account is created and no OTP is sent.
    adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
    adb('shell', 'monkey', '-p', package, '-c', 'android.intent.category.LAUNCHER', '1')
    wait_for('Welcome back')
    capture('09-resume')
    pid = adb('shell', 'pidof', package).strip().split()[0]
    log = adb('logcat', '-d', '--pid', pid, '-v', 'threadtime')
    if re.search(r'FATAL EXCEPTION|Fatal signal|ReactNativeJS:.*(?:TypeError|ReferenceError)', log):
        raise AssertionError('Native/JavaScript crash detected in emulator log')
    (out / 'result.txt').write_text('PASS: release APK installed, cold launch, onboarding, sign-in, password visibility, native input focus, keyboard dismissal, empty-field validation, password recovery and return, background/resume. No credentials or financial actions.\n')
    print((out / 'result.txt').read_text())


def main():
    global out
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', default='com.zitch.app')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', args.package):
        parser.error('Invalid Android package')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        run_smoke(args.package)
    finally:
        (out / 'logcat.txt').write_text(adb('logcat', '-d', '-v', 'threadtime', check=False))


if __name__ == '__main__':
    main()

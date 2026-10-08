#!/usr/bin/env python3
"""Observe input and notification behavior on a disposable Android emulator.

This diagnostic does not satisfy the release smoke gate. It installs no software,
clears the specified test app, enters no credentials, and submits no network form.
The caller must first install the exact APK being investigated.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET


def adb(*parts, binary=False, check=True):
    return subprocess.run(['adb', *parts], check=check, capture_output=True,
                          text=not binary, timeout=30).stdout


def hierarchy():
    adb('shell', 'uiautomator', 'dump', '/sdcard/zitch-diagnostics.xml')
    xml = adb('shell', 'cat', '/sdcard/zitch-diagnostics.xml')
    return xml, ET.fromstring(xml)


def matches(root, label):
    return [node for node in root.iter('node')
            if label in (node.get('text', ''), node.get('content-desc', ''))]


def wait_for(label, seconds=8):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            _, root = hierarchy()
            if matches(root, label):
                return root
        except (subprocess.SubprocessError, ET.ParseError) as error:
            last_error = str(error)
        time.sleep(0.5)
    raise AssertionError(f'Visible label not found: {label}; last error: {last_error}')


def press(label, method='tap'):
    root = wait_for(label)
    candidates = matches(root, label)
    # Prefer the accessible button over its nested text when both have the label.
    candidates.sort(key=lambda node: node.get('clickable') != 'true')
    node = candidates[0]
    if node.get('enabled') != 'true':
        raise AssertionError(f'Control disabled: {label}')
    bounds = node.get('bounds', '')
    values = [int(value) for value in re.findall(r'-?\d+', bounds)]
    if len(values) != 4:
        raise AssertionError(f'Invalid bounds for {label}: {bounds}')
    left, top, right, bottom = values
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise AssertionError(f'Control has no visible area: {label}: {bounds}')
    x, y = str((left + right) // 2), str((top + bottom) // 2)
    if method == 'tap':
        adb('shell', 'input', 'tap', x, y)
    elif method == 'press-200ms':
        adb('shell', 'input', 'swipe', x, y, x, y, '200')
    else:
        raise ValueError(f'Unsupported input method: {method}')
    return {'label': label, 'method': method, 'bounds': bounds, 'x': x, 'y': y}


def assert_empty_signin():
    _, root = hierarchy()
    placeholders = {'Email or phone': 'Email or phone number', 'Password': 'Enter password'}
    for label, placeholder in placeholders.items():
        fields = [node for node in matches(root, label)
                  if node.get('class') == 'android.widget.EditText']
        if len(fields) != 1 or fields[0].get('text', '') not in ('', placeholder):
            raise AssertionError(f'Refusing to submit a nonempty or unknown {label} field')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', default='com.zitch.app')
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', args.package):
        parser.error('Invalid Android package')
    # Refuse the destructive clear-data operation on a person's physical device.
    if adb('shell', 'getprop', 'ro.kernel.qemu').strip() != '1':
        raise RuntimeError('Diagnostics require a disposable Android emulator')
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {'diagnostic_only': True, 'package': args.package, 'observations': [],
              'animation_scales': {key: adb('shell', 'settings', 'get', 'global', key).strip()
                  for key in ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale')}}

    def capture(stage, observation):
        try:
            xml, root = hierarchy()
            (out / f'{stage}.xml').write_text(xml)
            observation['visible_labels'] = list(dict.fromkeys(
                value for node in root.iter('node')
                for value in (node.get('text', ''), node.get('content-desc', '')) if value))
        except (subprocess.SubprocessError, ET.ParseError) as error:
            observation['hierarchy_error'] = str(error)
        try:
            (out / f'{stage}.png').write_bytes(adb('exec-out', 'screencap', '-p', binary=True))
        except subprocess.SubprocessError as error:
            observation['screenshot_error'] = str(error)

    def observe(stage, action, expected):
        observation = {'stage': stage, 'at': datetime.now(timezone.utc).isoformat(),
                       'expected': expected, 'observed': False}
        try:
            observation['action'] = action()
            wait_for(expected)
            observation['observed'] = True
        except (AssertionError, subprocess.SubprocessError, ET.ParseError) as error:
            observation['error'] = str(error)
        capture(stage, observation)
        result['observations'].append(observation)
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({key: value for key, value in observation.items()
                          if key != 'visible_labels'}), flush=True)
        return observation['observed']

    def dismiss_notification():
        _, root = hierarchy()
        if matches(root, 'Close notification'):
            press('Close notification')
            try:
                wait_for('Welcome back', seconds=5)
            except AssertionError:
                # React Native Modal's hardware-back handler is another local
                # dismissal route; only use it while the dialog is still visible.
                _, current = hierarchy()
                if matches(current, 'Close notification'):
                    adb('shell', 'input', 'keyevent', 'KEYCODE_BACK')
        wait_for('Welcome back')

    def blank_signin(method):
        dismiss_notification()
        assert_empty_signin()
        return press('Sign in', method)

    def instant_signin():
        dismiss_notification()
        return press('Instant sign in')

    def forgot_password():
        dismiss_notification()
        return press('Forgot password?')

    try:
        adb('logcat', '-c')
        if adb('shell', 'pm', 'clear', args.package).strip() != 'Success':
            raise RuntimeError('Could not clear the installed test app')
        adb('shell', 'monkey', '-p', args.package, '-c', 'android.intent.category.LAUNCHER', '1')
        wait_for('Skip', seconds=60)
        observe('01-onboarding', lambda: {'method': 'observe'}, 'Skip')
        observe('02-signin', lambda: press('Skip'), 'Welcome back')
        toggled = observe('03-password-toggle', lambda: press('Show password'), 'Hide password')
        if toggled:
            observe('04-password-restore', lambda: press('Hide password'), 'Show password')
        observe('05-blank-signin-tap', lambda: blank_signin('tap'), 'Email or phone cannot be empty')
        observe('06-blank-signin-press', lambda: blank_signin('press-200ms'), 'Email or phone cannot be empty')
        observe('07-instant-signin', instant_signin, 'Biometric sign-in')
        # Only navigate to the recovery form; never press Send reset code.
        observe('08-forgot-password', forgot_password, 'Reset password')
        result['completed'] = True
    finally:
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        (out / 'logcat.txt').write_text(adb('logcat', '-d', '-v', 'threadtime', check=False))
        for name, parts in [('activity', ('activity', 'top')), ('input', ('input',)),
                            ('windows', ('window', 'windows'))]:
            (out / f'{name}.txt').write_text(adb('shell', 'dumpsys', *parts, check=False))
        print('Diagnostic observations saved; this does not replace the release smoke gate.', flush=True)


if __name__ == '__main__':
    main()

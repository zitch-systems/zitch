import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch
import xml.etree.ElementTree as ET


spec = importlib.util.spec_from_file_location(
    'android_smoke', Path(__file__).parents[1] / 'android-release-smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def field(label, text='', **attributes):
    return ET.Element('node', {
        'class': 'android.widget.EditText', 'content-desc': label, 'text': text,
        'enabled': 'true', 'clickable': 'true', 'focused': 'false',
        'bounds': '[20,100][200,150]', **attributes,
    })


def signin(email='', password=''):
    root = ET.Element('hierarchy')
    root.extend([field('Email or phone', email), field('Password', password)])
    return root


class AndroidReleaseSmokeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        smoke.out = Path(directory.name)

    def test_native_field_tap_does_not_select_the_separate_text_label(self):
        root = signin()
        root.insert(0, ET.Element('node', {
            'text': 'Email or phone', 'class': 'android.widget.TextView',
            'enabled': 'true', 'bounds': '[20,70][200,90]',
        }))
        with patch.object(smoke, 'wait_for', return_value=root), patch.object(smoke, 'adb') as adb:
            smoke.tap('Email or phone', attributes={'class': 'android.widget.EditText'})
        adb.assert_called_once_with('shell', 'input', 'tap', '110', '125')

    def test_disabled_or_invisible_controls_are_not_tapped(self):
        for attributes in ({'enabled': 'false'}, {'bounds': '[0,0][0,0]'},
                           {'bounds': '[-40,10][-20,50]'}, {'bounds': ''}):
            with self.subTest(attributes=attributes):
                root = ET.Element('hierarchy')
                root.append(field('Email or phone', **attributes))
                with patch.object(smoke, 'wait_for', return_value=root), patch.object(smoke, 'adb') as adb:
                    with self.assertRaises(AssertionError):
                        smoke.tap('Email or phone')
                adb.assert_not_called()

    def test_rendered_label_does_not_pass_native_focus_assertion(self):
        root = signin()
        with patch.object(smoke, 'hierarchy', return_value=('<hierarchy/>', root)), \
                patch.object(smoke.time, 'monotonic', side_effect=[0, 0, 46]), \
                patch.object(smoke.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'focused'):
                smoke.wait_for('Email or phone', attributes={
                    'class': 'android.widget.EditText', 'focused': 'true'})
        self.assertTrue((smoke.out / 'failed-ui.txt').exists())

    def test_empty_fields_accept_android_placeholder_representation(self):
        smoke.assert_empty_signin(signin())
        smoke.assert_empty_signin(signin('Email or phone number', 'Enter password'))

    def test_nonempty_missing_or_ambiguous_fields_refuse_submission(self):
        missing = signin()
        missing.remove(missing[1])
        duplicate = signin()
        duplicate.append(field('Password'))
        for root in (signin('account@example.test'), signin(password='secret'), missing, duplicate):
            with self.subTest(xml=ET.tostring(root, encoding='unicode')):
                with self.assertRaisesRegex(AssertionError, 'Refusing to submit'):
                    smoke.assert_empty_signin(root)

    def test_keyboard_unknown_state_is_not_treated_as_hidden(self):
        self.assertIs(smoke.keyboard_shown('mInputShown=true mInFullscreenMode=false'), True)
        self.assertIs(smoke.keyboard_shown('mInputShown=false mInFullscreenMode=false'), False)
        self.assertIsNone(smoke.keyboard_shown('InputMethod service unavailable'))
        with patch.object(smoke, 'adb', return_value='InputMethod service unavailable'), \
                patch.object(smoke.time, 'monotonic', side_effect=[0, 0, 16]), \
                patch.object(smoke.time, 'sleep'):
            with self.assertRaisesRegex(AssertionError, 'shown=False'):
                smoke.wait_for_keyboard(False)
        self.assertTrue((smoke.out / 'failed-keyboard.txt').exists())

    def test_physical_device_guard_precedes_every_mutation(self):
        with patch.object(smoke, 'adb', return_value='0\n') as adb:
            with self.assertRaisesRegex(RuntimeError, 'disposable Android emulator'):
                smoke.run_smoke('com.zitch.app')
        adb.assert_called_once_with('shell', 'getprop', 'ro.kernel.qemu')

    def test_failed_clear_data_prevents_app_launch(self):
        with patch.object(smoke, 'adb', side_effect=['1\n', '', 'Failed\n']) as adb:
            with self.assertRaisesRegex(RuntimeError, 'Could not clear'):
                smoke.run_smoke('com.zitch.app')
        self.assertEqual(adb.call_args_list, [
            call('shell', 'getprop', 'ro.kernel.qemu'), call('logcat', '-c'),
            call('shell', 'pm', 'clear', 'com.zitch.app'),
        ])

    def test_smoke_does_not_press_signin_when_any_field_is_nonempty(self):
        with patch.object(smoke, 'adb', side_effect=['1', '', 'Success', '', '', '']), \
                patch.object(smoke, 'wait_for'), patch.object(smoke, 'capture'), \
                patch.object(smoke, 'wait_for_keyboard'), patch.object(smoke, 'tap') as tap, \
                patch.object(smoke, 'hierarchy', return_value=('', signin('account@example.test'))):
            with self.assertRaisesRegex(AssertionError, 'Refusing to submit'):
                smoke.run_smoke('com.zitch.app')
        self.assertNotIn(call('Sign in'), tap.call_args_list)
        self.assertNotIn(call('Send reset code'), tap.call_args_list)


if __name__ == '__main__':
    unittest.main()

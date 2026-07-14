import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { router, Link, useLocalSearchParams } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import { notify } from '@/components/design/Notify';
import { PRIVACY_URL } from '@/components/configFiles/links';
import ZIcon from '@/components/design/ZIcon';
import { ZMark } from '@/components/design/Brand';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const Rule = ({ ok, text }: { ok: boolean; text: string }) => {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
      <View style={{ width: 18, height: 18, borderRadius: 9, backgroundColor: ok ? c.lime : c.surface3, alignItems: 'center', justifyContent: 'center' }}>
        <ZIcon name="check" size={11} color={ok ? '#fff' : c.ink3} stroke={3} />
      </View>
      <Text style={{ fontSize: 13, color: ok ? c.ink1 : c.ink3, fontFamily: font.regular }}>{text}</Text>
    </View>
  );
};

const SetPassword = () => {
  const { c } = useTheme();
  // `change=1` (from Security → Password) turns this into a CHANGE flow: the
  // current password is required and, on success, we return to Security rather
  // than continuing onboarding.
  const params = useLocalSearchParams<{ change?: string }>();
  const isChange = params.change === '1';
  const [isUpdating, setIsUpdating] = useState(false);
  const [token, setToken] = useState('');
  const [form, setForm] = useState({ current: '', password1: '', password2: '' });

  const p1 = form.password1;
  const eight = p1.length >= 8;
  const hasAlpha = /[A-Za-z]/.test(p1);
  const hasNum = /[0-9]/.test(p1);
  const hasSpecial = /[^A-Za-z0-9]/.test(p1);
  const tally = p1 !== '' && p1 === form.password2;
  const currentOk = !isChange || form.current.length > 0;
  const canSubmit = eight && hasAlpha && hasNum && hasSpecial && tally && currentOk;

  useEffect(() => {
    getToken().then((t) => t && setToken(t));
  }, []);

  const handleUpdate = async () => {
    if (!tally) {
      notify('Error', 'Passwords do not match');
      return;
    }
    setIsUpdating(true);
    try {
      const body: Record<string, string> = { password: p1 };
      if (isChange) body.current_password = form.current;
      const response = await apiPost('/api/set-password/', body);
      const result = await response.json();
      if (response.ok) {
        if (isChange) {
          notify('Password changed', 'Your account password has been updated.');
          router.back();
        } else {
          router.replace('/setpin');
        }
      } else if (result.code === 'current_password_required') {
        notify('Error', 'Your current password is incorrect.');
      } else {
        notify('Error', result.message || 'Could not set your password');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <Screen>
      {isChange ? (
        <Header title="Change password" sub="Confirm your current password, then set a new one" onBack={() => router.back()} />
      ) : (
        <>
          <View style={{ alignItems: 'center', marginTop: 14, marginBottom: 8 }}>
            <ZMark size={44} />
          </View>
          <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1 }}>Set up password</Text>
          <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 22, fontFamily: font.regular }}>
            Create a strong password for your account
          </Text>
        </>
      )}

      <View style={{ gap: 16 }}>
        {isChange && (
          <Field
            label="Current password"
            value={form.current}
            onChangeText={(e) => setForm({ ...form, current: e })}
            secureTextEntry
            placeholder="Enter current password"
            autoComplete="current-password"
            textContentType="password"
            prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
          />
        )}
        <Field
          label={isChange ? 'New password' : 'Password'}
          value={form.password1}
          onChangeText={(e) => setForm({ ...form, password1: e })}
          secureTextEntry
          placeholder={isChange ? 'Enter new password' : 'Enter password'}
          autoComplete="new-password"
          textContentType="newPassword"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="Confirm password"
          value={form.password2}
          onChangeText={(e) => setForm({ ...form, password2: e })}
          secureTextEntry
          placeholder="Re-enter password"
          autoComplete="new-password"
          textContentType="newPassword"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>

      <View style={{ marginTop: 16 }}>
        <Rule ok={eight} text="Must be at least 8 characters" />
        <Rule ok={hasAlpha} text="Must include an alphabet (Aa-Zz)" />
        <Rule ok={hasNum} text="Must include a number (0-9)" />
        <Rule ok={hasSpecial} text="Must include a special character (!@#$…)" />
      </View>

      <View style={{ marginTop: 26 }}>
        <Btn label={isChange ? 'Change password' : 'Continue'} disabled={!canSubmit || isUpdating} onPress={handleUpdate} />
      </View>
      {!isChange && (
        <Text style={{ fontSize: 12, color: c.ink3, marginTop: 14, lineHeight: 18, fontFamily: font.regular }}>
          By continuing you agree to our{' '}
          <Link href={PRIVACY_URL as any}>
            <Text style={{ color: c.brand, fontFamily: font.semibold }}>Privacy Policy & Terms</Text>
          </Link>
          .
        </Text>
      )}
    </Screen>
  );
};

export default SetPassword;

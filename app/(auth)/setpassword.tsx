import React, { useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, Link, useLocalSearchParams } from 'expo-router';
import { saveRefreshToken } from '@/lib/secureStore';
import AuthGuard from '@/components/AuthGuard';
import { usePinScreenProtection } from '@/lib/screenCapture';
import { apiPost } from '@/lib/api';
import { EP } from '@/lib/endpoints';
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
  const [isUpdating, setIsUpdating] = useState(false);
  const params = useLocalSearchParams<{ change?: string }>();
  const [changing, setChanging] = useState(params.change === '1');
  const [currentPassword, setCurrentPassword] = useState('');
  const inFlight = useRef(false);
  usePinScreenProtection();
  const [form, setForm] = useState({ password1: '', password2: '' });

  const p1 = form.password1;
  const eight = p1.length >= 8;
  const hasLetter = /[A-Za-z]/.test(p1);
  const hasSpecial = /[^A-Za-z0-9]/.test(p1);
  const hasNum = /\d/.test(p1);
  const tally = p1 !== '' && p1 === form.password2;
  const canSubmit = eight && hasLetter && hasSpecial && hasNum && tally && (!changing || !!currentPassword);

  const handleUpdate = async () => {
    if (!canSubmit || inFlight.current) return;
    if (!tally) {
      notify('Error', 'Passwords do not match');
      return;
    }
    inFlight.current = true;
    setIsUpdating(true);
    try {
      const response = await apiPost(EP.auth.setPassword, { password: p1, ...(changing ? { current_password: currentPassword } : {}) });
      const result = await response.json();
      if (response.ok) {
        // Password changes revoke the previous refresh chain, including ours.
        await saveRefreshToken(result.refresh_token || '');
        if (changing) { notify('Password changed'); router.back(); }
        else router.replace('/setpin');
      } else {
        if (result.code === 'current_password_required') setChanging(true);
        notify('Error', result.message || 'Could not set your password');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      inFlight.current = false;
      setIsUpdating(false);
    }
  };

  return (
    <Screen>
      {changing && <Header onBack={() => router.back()} />}
      <View style={{ alignItems: 'center', marginTop: 14, marginBottom: 8 }}>
        <ZMark size={44} />
      </View>
      <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1 }}>{changing ? 'Change password' : 'Set up password'}</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 22, fontFamily: font.regular }}>
        Create a strong password for your account
      </Text>

      <View style={{ gap: 16 }}>
        {changing && <Field label="Current password" value={currentPassword} onChangeText={setCurrentPassword}
          secureTextEntry autoComplete="current-password" textContentType="password" placeholder="Enter current password" />}
        <Field
          label="Password"
          value={form.password1}
          onChangeText={(e) => setForm({ ...form, password1: e })}
          secureTextEntry
          autoComplete="new-password"
          textContentType="newPassword"
          placeholder="Enter password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <View>
          <Field
            label="Confirm password"
            value={form.password2}
            onChangeText={(e) => setForm({ ...form, password2: e })}
            secureTextEntry
            autoComplete="new-password"
            textContentType="newPassword"
            placeholder="Re-enter password"
            prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
          />
          {form.password2.length > 0 && (
            <Text style={{ fontSize: 12, color: tally ? c.lime : c.red, marginTop: 6, marginLeft: 2, fontFamily: font.semibold }}>
              {tally ? 'Passwords match' : 'Passwords do not match'}
            </Text>
          )}
        </View>
      </View>

      <View style={{ marginTop: 16 }}>
        <Rule ok={eight} text="8+ characters" />
        <Rule ok={hasLetter} text="1 letter" />
        <Rule ok={hasSpecial} text="1 special character" />
        <Rule ok={hasNum} text="1 number" />
      </View>

      <View style={{ marginTop: 26 }}>
        <Btn label={changing ? "Change password" : "Continue"} disabled={!canSubmit || isUpdating} onPress={handleUpdate} />
      </View>
      <Text style={{ fontSize: 12, color: c.ink3, marginTop: 14, lineHeight: 18, fontFamily: font.regular }}>
        By continuing you agree to our{' '}
        <Link href={PRIVACY_URL as any}>
          <Text style={{ color: c.brand, fontFamily: font.semibold }}>Privacy Policy & Terms</Text>
        </Link>
        .
      </Text>
    </Screen>
  );
};

export default function GuardedSetPassword() { return <AuthGuard fresh><SetPassword /></AuthGuard>; }

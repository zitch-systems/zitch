import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router, Link } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import { PRIVACY_URL } from '@/components/configFiles/links';
import ZIcon from '@/components/design/ZIcon';
import { ZMark } from '@/components/design/Brand';
import { Screen, Field, Btn } from '@/components/design/ui';
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
  const [token, setToken] = useState('');
  const [form, setForm] = useState({ password1: '', password2: '' });

  const p1 = form.password1;
  const eight = p1.length >= 8;
  const hasAlpha = /[A-Za-z]/.test(p1);
  const hasNum = /[0-9]/.test(p1);
  const tally = p1 !== '' && p1 === form.password2;
  const canSubmit = eight && hasAlpha && hasNum && tally;

  useEffect(() => {
    getToken().then((t) => t && setToken(t));
  }, []);

  const handleUpdate = async () => {
    if (!tally) {
      Alert.alert('Error', 'Passwords do not match');
      return;
    }
    setIsUpdating(true);
    try {
      const response = await apiPost('/api/set-password/', { password: p1 });
      const result = await response.json();
      if (response.ok) {
        router.replace('/setpin');
      } else {
        Alert.alert('Error', result.message || 'Could not set your password');
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <Screen>
      <View style={{ alignItems: 'center', marginTop: 14, marginBottom: 8 }}>
        <ZMark size={44} />
      </View>
      <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1 }}>Set up password</Text>
      <Text style={{ fontSize: 14, color: c.ink3, marginTop: 6, marginBottom: 22, fontFamily: font.regular }}>
        Create a strong password for your account
      </Text>

      <View style={{ gap: 16 }}>
        <Field
          label="Password"
          value={form.password1}
          onChangeText={(e) => setForm({ ...form, password1: e })}
          secureTextEntry
          placeholder="Enter password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
        <Field
          label="Confirm password"
          value={form.password2}
          onChangeText={(e) => setForm({ ...form, password2: e })}
          secureTextEntry
          placeholder="Re-enter password"
          prefix={<ZIcon name="lock" size={18} color={c.ink3} />}
        />
      </View>

      <View style={{ marginTop: 16 }}>
        <Rule ok={eight} text="Must be at least 8 characters" />
        <Rule ok={hasAlpha} text="Must include an alphabet (Aa-Zz)" />
        <Rule ok={hasNum} text="Must include a number (0-9)" />
      </View>

      <View style={{ marginTop: 26 }}>
        <Btn label="Continue" disabled={!canSubmit || isUpdating} onPress={handleUpdate} />
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

export default SetPassword;

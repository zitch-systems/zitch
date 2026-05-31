import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import baseUrl from '@/components/configFiles/apiConfig';
import { ZMark } from '@/components/design/Brand';
import { Screen } from '@/components/design/ui';
import { Keypad } from '@/components/design/Keypad';
import { useTheme, font } from '@/lib/theme';

const PIN_LEN = 4;

const SetPin = () => {
  const { c } = useTheme();
  const [pin, setPin] = useState('');
  const [confirm, setConfirm] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  const [memoryEmail, setMemoryEmail] = useState('');

  const active = confirm === null ? pin : confirm;

  useEffect(() => {
    AsyncStorage.getItem('UserEmail').then((e) => e && setMemoryEmail(e));
  }, []);

  const submit = async (finalPin: string) => {
    try {
      const response = await fetch(`${baseUrl}/api/set-transaction-pin/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: memoryEmail, pin: finalPin }),
      });
      const result = await response.json();
      if (response.ok) {
        router.replace('/completed');
      } else {
        Alert.alert('Error', result.message || 'Could not set your PIN');
        setConfirm('');
      }
    } catch (error) {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
      setConfirm('');
    }
  };

  // Drive the create → confirm → submit flow.
  useEffect(() => {
    if (confirm === null && pin.length === PIN_LEN) {
      const t = setTimeout(() => setConfirm(''), 180);
      return () => clearTimeout(t);
    }
    if (confirm !== null && confirm.length === PIN_LEN) {
      if (confirm === pin) {
        const t = setTimeout(() => submit(pin), 220);
        return () => clearTimeout(t);
      }
      setErr(true);
      const t = setTimeout(() => { setErr(false); setConfirm(''); }, 700);
      return () => clearTimeout(t);
    }
  }, [pin, confirm]); // eslint-disable-line react-hooks/exhaustive-deps

  const onKey = (k: string) => {
    if (confirm === null) {
      setPin((p) => (k === 'del' ? p.slice(0, -1) : p.length < PIN_LEN ? p + k : p));
    } else {
      setConfirm((cf) => (k === 'del' ? (cf || '').slice(0, -1) : (cf || '').length < PIN_LEN ? (cf || '') + k : cf));
    }
  };

  return (
    <Screen scroll={false}>
      <View style={{ flex: 1, alignItems: 'center' }}>
        <View style={{ marginTop: 26 }}>
          <ZMark size={44} />
        </View>
        <Text style={{ fontSize: 22, fontFamily: font.extrabold, color: c.ink1, marginTop: 20 }}>
          {confirm === null ? 'Create a 4-digit PIN' : 'Confirm your PIN'}
        </Text>
        <Text style={{ fontSize: 14, color: err ? c.red : c.ink3, marginTop: 6, textAlign: 'center', fontFamily: err ? font.bold : font.regular }}>
          {err ? "PINs don't match, try again" : "You'll use this to authorize payments"}
        </Text>

        <View style={{ flexDirection: 'row', gap: 18, marginVertical: 30 }}>
          {Array.from({ length: PIN_LEN }).map((_, k) => {
            const filled = active.length > k;
            const color = err ? c.red : c.brand;
            return (
              <View
                key={k}
                style={{
                  width: 18,
                  height: 18,
                  borderRadius: 10,
                  backgroundColor: filled ? color : 'transparent',
                  borderWidth: 2,
                  borderColor: filled ? color : c.line,
                }}
              />
            );
          })}
        </View>

        <View style={{ flex: 1 }} />
        <View style={{ width: '100%', paddingBottom: 24 }}>
          <Keypad onKey={onKey} />
        </View>
      </View>
    </Screen>
  );
};

export default SetPin;

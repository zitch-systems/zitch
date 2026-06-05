import React, { useEffect, useState } from 'react';
import { View, Text, Alert } from 'react-native';
import { router } from 'expo-router';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getToken } from '@/lib/secureStore';
import { apiPost } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Header, Field, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const AccountDetails = () => {
  const { c } = useTheme();
  const [isUpdating, setIsUpdating] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [current, setCurrent] = useState({ firstName: '', lastName: '', email: '', phone: '' });
  const [form, setForm] = useState({ firstName: '', lastName: '', email: '', phone: '' });

  useEffect(() => {
    getToken().then(setToken);
  }, []);

  useEffect(() => {
    if (!token) return;
    apiPost('/api/wallet_balance/')
      .then((r) => r.json())
      .then((data) => {
        if (data.success) {
          setCurrent({
            firstName: data.user_first_name ?? '',
            lastName: data.user_last_name ?? '',
            email: data.user_email ?? '',
            phone: data.user_phone_number ?? '',
          });
        }
      })
      .catch(() => {});
  }, [token]);

  const handleUpdate = async () => {
    if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      Alert.alert('Invalid email', 'Enter a valid email address.');
      return;
    }
    if (form.phone && form.phone.length !== 11) {
      Alert.alert('Invalid phone', 'Enter a valid 11-digit phone number.');
      return;
    }
    setIsUpdating(true);
    try {
      const response = await apiPost('/api/update_info/', {
        email: form.email || current.email,
        phone: form.phone || current.phone,
        first_name: form.firstName || current.firstName,
        last_name: form.lastName || current.lastName,
      });
      const result = await response.json();
      if (response.ok) {
        if (form.email) await AsyncStorage.setItem('UserEmail', form.email);
        if (form.phone) await AsyncStorage.setItem('UserPhone', form.phone);
        Alert.alert('Success', 'Account updated');
      } else {
        Alert.alert('Error', result.message || 'Failed to update account');
      }
    } catch {
      Alert.alert('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <Screen>
      <Header title="Account Details" sub="Your account profile details" onBack={() => router.back()} />

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginBottom: 20 }}>
        <Avatar size={64} ring={c.brand} surface={c.surface} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 16 }}>
            {current.firstName} {current.lastName}
          </Text>
          <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{current.phone}</Text>
        </View>
        <Btn label="Update photo" variant="outline" size="sm" full={false} onPress={() => router.push('/comingsoon')} />
      </View>

      <View style={{ gap: 16 }}>
        <Field label="First name" value={form.firstName} onChangeText={(e) => setForm({ ...form, firstName: e })} placeholder={current.firstName || 'First name'} prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
        <Field label="Last name" value={form.lastName} onChangeText={(e) => setForm({ ...form, lastName: e })} placeholder={current.lastName || 'Last name'} prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
        <Field label="Email" value={form.email} onChangeText={(e) => setForm({ ...form, email: e })} keyboardType="email-address" placeholder={current.email || 'you@email.com'} prefix={<ZIcon name="remita" size={18} color={c.ink3} />} />
        <Field label="Phone" value={form.phone} onChangeText={(e) => setForm({ ...form, phone: e.replace(/\D/g, '').slice(0, 11) })} keyboardType="number-pad" placeholder={current.phone || '0801 234 5678'} prefix={<ZIcon name="airtime" size={18} color={c.ink3} />} />
      </View>

      <View style={{ marginTop: 26 }}>
        <Btn label="Update Profile" onPress={handleUpdate} disabled={isUpdating} />
      </View>
    </Screen>
  );
};

export default AccountDetails;

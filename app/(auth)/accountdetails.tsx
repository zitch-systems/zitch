import React, { useEffect, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as ImagePicker from 'expo-image-picker';
import { getToken } from '@/lib/secureStore';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { apiPost } from '@/lib/api';
import { useWallet } from '@/lib/wallet';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Header, Field, Btn, Sheet } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';
import AuthGuard from '@/components/AuthGuard';

const AccountDetails = () => {
  const { c } = useTheme();
  const { reload: reloadWallet } = useWallet();
  const [isUpdating, setIsUpdating] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [avatar, setAvatar] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [current, setCurrent] = useState({ firstName: '', lastName: '', email: '', phone: '' });
  const [form, setForm] = useState({ firstName: '', lastName: '', email: '', phone: '' });
  // Changing the email or phone re-authenticates: those two fields decide where a
  // password reset is delivered, so the server refuses the change on a session
  // token alone (code "reauth_required"). Held here so the customer can supply it
  // and have the save go through, instead of meeting a bare "failed to update".
  const [reauthOpen, setReauthOpen] = useState(false);
  const [reauthPassword, setReauthPassword] = useState('');

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
          setAvatar(data.user_avatar ?? '');
        }
      })
      .catch(() => {});
  }, [token]);

  const updatePhoto = async () => {
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) {
      notify('Permission needed', 'Allow photo access to update your picture.');
      return;
    }
    beginExternalActivity(); // keep the app-lock from firing while the picker is up
    let res;
    try {
      res = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ImagePicker.MediaTypeOptions.Images,
        allowsEditing: true,
        aspect: [1, 1],
        quality: 0.6,
        base64: true,
      });
    } finally { endExternalActivity(); }
    if (res.canceled || !res.assets?.[0]?.base64) return;
    const asset = res.assets[0];
    setAvatar(asset.uri); // optimistic local preview
    setUploadingPhoto(true);
    try {
      const r = await apiPost('/api/profile/avatar/', { image: `data:image/jpeg;base64,${asset.base64}` });
      const body = await r.json();
      if (r.ok && body.success) {
        setAvatar(body.avatar);
        reloadWallet(); // refresh the photo shown on home/profile headers
      } else {
        notify('Error', body.message || 'Could not update photo');
      }
    } catch {
      notify('Error', 'Something went wrong uploading your photo.');
    } finally {
      setUploadingPhoto(false);
    }
  };

  const handleUpdate = async () => {
    if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      notify('Invalid email', 'Enter a valid email address.');
      return;
    }
    if (form.phone && form.phone.length !== 11) {
      notify('Invalid phone', 'Enter a valid 11-digit phone number.');
      return;
    }
    await submitUpdate();
  };

  const submitUpdate = async (password?: string) => {
    setIsUpdating(true);
    try {
      const response = await apiPost('/api/update_info/', {
        email: form.email || current.email,
        phone: form.phone || current.phone,
        first_name: form.firstName || current.firstName,
        last_name: form.lastName || current.lastName,
        ...(password ? { password } : {}),
      });
      const result = await response.json();
      if (response.ok) {
        if (form.email) await AsyncStorage.setItem('UserEmail', form.email);
        if (form.phone) await AsyncStorage.setItem('UserPhone', form.phone);
        setReauthOpen(false);
        setReauthPassword('');
        // Say so plainly: the new address has to be confirmed again before it
        // counts towards the KYC tier, and the customer would otherwise only
        // discover that at the next limit check.
        const dropped = result?.email_verified === false || result?.phone_verified === false;
        notify('Success', dropped
          ? 'Account updated. Confirm your new email or phone to restore your verification.'
          : 'Account updated');
      } else if (result.code === 'reauth_required') {
        setReauthOpen(true);
      } else {
        setReauthPassword('');
        notify('Error', result.message || 'Failed to update account');
      }
    } catch {
      notify('Error', 'Something went wrong. Please try again later.');
    } finally {
      setIsUpdating(false);
    }
  };

  return (
    <Screen>
      <Header title="Account Details" sub="Your account profile details" onBack={() => router.back()} />

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, marginBottom: 20 }}>
        <Avatar size={64} ring={c.brand} surface={c.surface} uri={avatar} />
        <View style={{ flex: 1 }}>
          <Text style={{ fontFamily: font.bold, color: c.ink1, fontSize: 16 }}>
            {current.firstName} {current.lastName}
          </Text>
          <Text style={{ fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>{current.phone}</Text>
        </View>
        <Btn label={uploadingPhoto ? 'Uploading…' : 'Update photo'} variant="outline" size="sm" full={false} disabled={uploadingPhoto} onPress={updatePhoto} />
      </View>

      <View style={{ gap: 16 }}>
        <Field label="First name" value={form.firstName} onChangeText={(e) => setForm({ ...form, firstName: e })} placeholder={current.firstName || 'First name'} prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
        <Field label="Last name" value={form.lastName} onChangeText={(e) => setForm({ ...form, lastName: e })} placeholder={current.lastName || 'Last name'} prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
        <Field label="Email" value={form.email} onChangeText={(e) => setForm({ ...form, email: e })} keyboardType="email-address" placeholder={current.email || 'you@email.com'} prefix={<ZIcon name="mail" size={18} color={c.ink3} />} />
        <Field label="Phone" value={form.phone} onChangeText={(e) => setForm({ ...form, phone: e.replace(/\D/g, '').slice(0, 11) })} keyboardType="number-pad" placeholder={current.phone || '0801 234 5678'} prefix={<ZIcon name="airtime" size={18} color={c.ink3} />} />
      </View>

      <View style={{ marginTop: 26 }}>
        <Btn label="Update Profile" onPress={handleUpdate} disabled={isUpdating} />
      </View>

      <Sheet open={reauthOpen} onClose={() => { setReauthOpen(false); setReauthPassword(''); }}
             title="Confirm it's you">
        <Text style={{ fontSize: 13.5, color: c.ink3, fontFamily: font.regular, lineHeight: 20, marginBottom: 14 }}>
          Your email and phone number are how we send account-recovery codes, so
          changing either one needs your password.
        </Text>
        <Field label="Password" value={reauthPassword} onChangeText={setReauthPassword}
               secureTextEntry placeholder="Your account password"
               prefix={<ZIcon name="lock" size={18} color={c.ink3} />} />
        <View style={{ height: 16 }} />
        <Btn label={isUpdating ? 'Saving…' : 'Confirm and save'}
             disabled={isUpdating || reauthPassword.length < 1}
             onPress={() => submitUpdate(reauthPassword)} />
      </Sheet>
    </Screen>
  );
};

// Post-login screen living in the unguarded (auth) group: gate it explicitly
// so a deep link can't render it without a valid, unlocked session (the API
// would 401 anyway — this keeps the surface consistent with the other groups).
const GuardedAccountDetails = () => (
  <AuthGuard>
    <AccountDetails />
  </AuthGuard>
);

export default GuardedAccountDetails;

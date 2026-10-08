import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router } from 'expo-router';
import { notify } from '@/components/design/Notify';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as ImagePicker from 'expo-image-picker';
import AuthGuard from '@/components/AuthGuard';
import { getToken } from '@/lib/secureStore';
import { beginExternalActivity, endExternalActivity } from '@/lib/session';
import { apiPost } from '@/lib/api';
import { EP } from '@/lib/endpoints';
import { useWallet } from '@/lib/wallet';
import ZIcon from '@/components/design/ZIcon';
import { Avatar } from '@/components/design/Brand';
import { Screen, Header, Field, Btn, Tap } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

const AccountDetails = () => {
  const { c } = useTheme();
  const { reload: reloadWallet } = useWallet();
  const [isUpdating, setIsUpdating] = useState(false);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [avatar, setAvatar] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [current, setCurrent] = useState({ firstName: '', lastName: '', email: '', phone: '' });
  const [form, setForm] = useState({ firstName: '', lastName: '', email: '' });
  const [password, setPassword] = useState('');
  const inFlight = useRef(false);
  const photoInFlight = useRef(false);

  useEffect(() => {
    getToken().then(setToken);
  }, []);

  useEffect(() => {
    if (!token) return;
    apiPost(EP.wallet.balance)
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
    if (photoInFlight.current) return;
    photoInFlight.current = true;
    const previousAvatar = avatar;
    try {
      const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
      if (!perm.granted) { notify('Permission needed', 'Allow photo access to update your picture.'); return; }
      beginExternalActivity();
      let res;
      try {
        res = await ImagePicker.launchImageLibraryAsync({
          mediaTypes: ImagePicker.MediaTypeOptions.Images, allowsEditing: true,
          aspect: [1, 1], quality: 0.6, base64: true,
        });
      } finally { endExternalActivity(); }
      if (res.canceled || !res.assets?.[0]?.base64) return;
      const asset = res.assets[0];
      setAvatar(asset.uri);
      setUploadingPhoto(true);
      const r = await apiPost(EP.auth.avatar, { image: `data:${asset.mimeType || 'image/jpeg'};base64,${asset.base64}` });
      const body = await r.json();
      if (r.ok && body.success) {
        setAvatar(body.avatar);
        void reloadWallet();
      } else {
        setAvatar(previousAvatar);
        notify('Error', body.message || 'Could not update photo');
      }
    } catch {
      setAvatar(previousAvatar);
      notify('Error', 'Could not update your photo. Please try again.');
    } finally {
      photoInFlight.current = false;
      setUploadingPhoto(false);
    }
  };

  // Gate "Save changes": only enable once something changed and the email is valid.
  const emailOk = !form.email || /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email);
  const dirty = !!(form.firstName || form.lastName || form.email);
  const changingEmail = !!form.email.trim() && form.email.trim().toLowerCase() !== current.email.toLowerCase();
  const canSave = dirty && emailOk && (!changingEmail || !!password);

  const handleUpdate = async () => {
    if (!canSave || inFlight.current) return;
    if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) {
      notify('Invalid email', 'Enter a valid email address.');
      return;
    }
    inFlight.current = true;
    setIsUpdating(true);
    try {
      const response = await apiPost(EP.auth.updateInfo, {
        email: form.email.trim() || current.email,
        first_name: form.firstName.trim() || current.firstName,
        last_name: form.lastName.trim() || current.lastName,
        ...(changingEmail ? { password } : {}),
      });
      const result = await response.json();
      if (response.ok) {
        if (form.email) await AsyncStorage.setItem('UserEmail', form.email.trim());
        setCurrent((previous) => ({ ...previous, firstName: form.firstName.trim() || previous.firstName, lastName: form.lastName.trim() || previous.lastName, email: form.email.trim() || previous.email }));
        setForm({ firstName: '', lastName: '', email: '' });
        setPassword('');
        void reloadWallet();
        notify('Profile updated');
      } else {
        notify('Error', result.message || 'Failed to update account');
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
      <Header title="Account Details" sub="Your account profile details" onBack={() => router.back()} />

      <View style={{ alignItems: 'center', marginBottom: 18 }}>
        <View style={{ position: 'relative' }}>
          <Avatar size={84} ring={c.brand} surface={c.surface} uri={avatar} />
          <View style={{ position: 'absolute', right: -2, bottom: -2, width: 30, height: 30, borderRadius: 15, backgroundColor: c.brand, borderWidth: 3, borderColor: c.surface, alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="plus" size={15} color="#fff" stroke={2.6} />
          </View>
        </View>
        <Tap onPress={updatePhoto} disabled={uploadingPhoto}>
          <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.brand, marginTop: 10 }}>
            {uploadingPhoto ? 'Uploading…' : 'Change photo'}
          </Text>
        </Tap>
      </View>

      <View style={{ gap: 16 }}>
        <Field label="First name" value={form.firstName} onChangeText={(e) => setForm({ ...form, firstName: e })} placeholder={current.firstName || 'First name'} prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
        <Field label="Last name" value={form.lastName} onChangeText={(e) => setForm({ ...form, lastName: e })} placeholder={current.lastName || 'Last name'} prefix={<ZIcon name="user" size={18} color={c.ink3} />} />
        <Field label="Email" value={form.email} onChangeText={(e) => setForm({ ...form, email: e })} keyboardType="email-address" placeholder={current.email || 'you@email.com'} prefix={<ZIcon name="remita" size={18} color={c.ink3} />} />
        {changingEmail && <Field label="Account password" value={password} onChangeText={setPassword}
          secureTextEntry autoComplete="current-password" placeholder="Confirm password to change email" />}
        <View>
          <Field label="Verified phone" value={current.phone} editable={false} prefix={<ZIcon name="airtime" size={18} color={c.ink3} />} />
          <Text style={{ fontSize: 12, color: c.ink3, fontFamily: font.regular, lineHeight: 18, marginTop: 7 }}>
            To change your verified phone number, contact support so we can protect account recovery and transfers.
          </Text>
          <Tap
            onPress={() => router.push('/support')}
            accessibilityLabel="Contact support to change verified phone"
            hitSlop={8}
            style={{ alignSelf: 'flex-start', marginTop: 6 }}
          >
            <Text style={{ fontSize: 12.5, color: c.brand, fontFamily: font.semibold }}>Contact support</Text>
          </Tap>
        </View>
      </View>

      <View style={{ marginTop: 26 }}>
        <Btn label="Save changes" onPress={handleUpdate} disabled={isUpdating || !canSave} />
      </View>
    </Screen>
  );
};

export default function GuardedAccountDetails() { return <AuthGuard fresh><AccountDetails /></AuthGuard>; }

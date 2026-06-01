import React, { useCallback, useState } from 'react';
import { View, Text, Pressable, Image, Alert } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import baseUrl from '@/components/configFiles/apiConfig';
import { getToken } from '@/lib/secureStore';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Card, Btn } from '@/components/design/ui';
import { useTheme, font } from '@/lib/theme';

type VCard = { id: number; brand: string; masked: string; expiry: string; holder: string; frozen: boolean };

const Cards = () => {
  const { c } = useTheme();
  const [token, setToken] = useState('');
  const [card, setCard] = useState<VCard | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const t = await getToken();
    if (!t) return;
    setToken(t);
    try {
      const res = await fetch(`${baseUrl}/api/cards/list/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: t }),
      }).then((r) => r.json());
      setCard(res.cards?.[0] ?? null);
    } catch { /* keep last state */ }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const createCard = async () => {
    setBusy(true);
    try {
      const res = await fetch(`${baseUrl}/api/cards/create/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token }),
      }).then((r) => r.json());
      if (res.success) setCard(res.card);
      else Alert.alert('Error', res.message || 'Could not create card');
    } catch { Alert.alert('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  const toggleFreeze = async () => {
    if (!card) return;
    try {
      const res = await fetch(`${baseUrl}/api/cards/freeze/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: token, card_id: card.id }),
      }).then((r) => r.json());
      if (res.success) setCard(res.card);
    } catch { Alert.alert('Error', 'Something went wrong.'); }
  };

  const frozen = card?.frozen ?? false;

  return (
    <Screen pad={false} tab>
      <Text style={{ paddingHorizontal: 20, paddingTop: 6, fontSize: 26, fontFamily: font.extrabold, color: c.ink1 }}>Cards</Text>

      {card ? (
        <>
          {/* card visual */}
          <LinearGradient
            colors={frozen ? ['#1B463C', '#0B2A24'] : ['#0C5249', '#0FA295', '#5CF5EB']}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{ margin: 16, borderRadius: 22, padding: 20, height: 200, overflow: 'hidden', shadowColor: '#000', shadowOpacity: 0.5, shadowRadius: 24, shadowOffset: { width: 0, height: 18 }, elevation: 8 }}
          >
            <Image source={require('@/assets/images/zitch-mark.png')} style={{ position: 'absolute', right: -20, bottom: -30, width: 160, height: 160, opacity: 0.22 }} resizeMode="contain" />
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontFamily: font.bold, letterSpacing: 1.3 }}>ZITCH</Text>
              <ZIcon name="wallet" size={22} color="#fff" />
            </View>
            <Text style={{ color: '#fff', fontSize: 21, letterSpacing: 3, marginTop: 46, fontVariant: ['tabular-nums'] }}>{card.masked}</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 16 }}>
              <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontFamily: font.semibold }}>{card.holder}</Text>
              <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontVariant: ['tabular-nums'] }}>{card.expiry}</Text>
            </View>
            {frozen && (
              <View style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(5,32,28,.4)' }}>
                <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 14, letterSpacing: 1.4 }}>❄ FROZEN</Text>
              </View>
            )}
          </LinearGradient>

          {/* actions */}
          <View style={{ flexDirection: 'row', gap: 10, marginHorizontal: 16 }}>
            {[
              { icon: 'lock', label: frozen ? 'Unfreeze' : 'Freeze', go: toggleFreeze },
              { icon: 'eye', label: 'Details', go: () => router.push('/comingsoon') },
              { icon: 'settings', label: 'Settings', go: () => router.push('/comingsoon') },
            ].map((a) => (
              <Pressable key={a.label} onPress={a.go} style={{ flex: 1 }}>
                <View style={{ alignItems: 'center', gap: 8, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
                  <ZIcon name={a.icon} size={20} color={c.brand} />
                  <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{a.label}</Text>
                </View>
              </Pressable>
            ))}
          </View>
        </>
      ) : (
        /* empty state */
        <View style={{ alignItems: 'center', paddingTop: 48, paddingHorizontal: 24 }}>
          <View style={{ width: 88, height: 88, borderRadius: 28, backgroundColor: 'rgba(15,162,149,.12)', alignItems: 'center', justifyContent: 'center' }}>
            <ZIcon name="card" size={40} color={c.brand} />
          </View>
          <Text style={{ fontSize: 18, fontFamily: font.extrabold, color: c.ink1, marginTop: 20 }}>No card yet</Text>
          <Text style={{ fontSize: 14, color: c.ink3, marginTop: 8, textAlign: 'center', maxWidth: 280, fontFamily: font.regular }}>
            Create a free virtual card for online & USD payments.
          </Text>
          <View style={{ height: 20 }} />
          <Btn label="Create a virtual card" icon="plus" disabled={busy} onPress={createCard} full={false} />
        </View>
      )}
    </Screen>
  );
};

export default Cards;

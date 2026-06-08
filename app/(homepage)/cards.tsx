import React, { useCallback, useRef, useState } from 'react';
import { View, Text, Pressable, Image, Alert } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useFocusEffect } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiJson, newIdempotencyKey } from '@/lib/api';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Btn, Field, Sheet, PinPad, money } from '@/components/design/ui';
import { QuickAmounts } from '@/components/design/flowkit';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

type VCard = { id: number; brand: string; masked: string; expiry: string; holder: string; balance: string; frozen: boolean };
type Reveal = { pan: string; cvv: string; expiry: string; holder: string };
const FUND_AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

const Cards = () => {
  const { c } = useTheme();
  const { reload: reloadWallet } = useWallet();
  const [token, setToken] = useState('');
  const [card, setCard] = useState<VCard | null>(null);
  const [busy, setBusy] = useState(false);

  // sheets
  const [fundOpen, setFundOpen] = useState(false);
  const [fundAmt, setFundAmt] = useState('');
  const [fundPin, setFundPin] = useState(false);
  const [detailsPin, setDetailsPin] = useState(false);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [pinError, setPinError] = useState('');

  const load = useCallback(async () => {
    const t = await getToken();
    if (!t) return;
    setToken(t);
    try {
      const res = await apiJson('/api/cards/list/');
      setCard(res.cards?.[0] ?? null);
    } catch { /* keep last state */ }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const createCard = async () => {
    setBusy(true);
    try {
      const res = await apiJson('/api/cards/create/');
      if (res.success) setCard(res.card);
      else Alert.alert('Error', res.message || 'Could not create card');
    } catch { Alert.alert('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  const toggleFreeze = async () => {
    if (!card) return;
    try {
      const res = await apiJson('/api/cards/freeze/', { card_id: card.id });
      if (res.success) setCard(res.card);
    } catch { Alert.alert('Error', 'Something went wrong.'); }
  };

  const idemKey = useRef('');  // stable across retries of one card-funding attempt

  const doFund = async (pin: string) => {
    if (!card) return;
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const res = await apiJson('/api/cards/fund/', { card_id: card.id, amount: fundAmt, transaction_pin: pin, idempotency_key: idemKey.current });
      if (res.success) { idemKey.current = ''; setFundPin(false); setPinError(''); setCard(res.card); setFundAmt(''); reloadWallet(); Alert.alert('Success', 'Card funded'); }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else { idemKey.current = ''; setFundPin(false); Alert.alert('Error', res.message || 'Funding failed'); }
    } catch { setFundPin(false); Alert.alert('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  const doReveal = async (pin: string) => {
    if (!card) return;
    setBusy(true);
    try {
      const res = await apiJson('/api/cards/details/', { card_id: card.id, transaction_pin: pin });
      if (res.success) { setDetailsPin(false); setPinError(''); setReveal({ pan: res.pan, cvv: res.cvv, expiry: res.expiry, holder: res.holder }); }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else { setDetailsPin(false); Alert.alert('Error', res.message || 'Could not fetch details'); }
    } catch { setDetailsPin(false); Alert.alert('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  const frozen = card?.frozen ?? false;
  const panGroups = reveal ? reveal.pan.replace(/(.{4})/g, '$1 ').trim() : '';

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
            <Text style={{ color: '#fff', fontSize: 21, letterSpacing: 3, marginTop: 30, fontFamily: font.semibold, fontVariant: ['tabular-nums'] }}>{reveal ? panGroups : card.masked}</Text>
            <Text style={{ color: 'rgba(255,255,255,.85)', fontSize: 12.5, marginTop: 8, fontFamily: font.medium, fontVariant: ['tabular-nums'] }}>Balance {money(Number(card.balance))}{reveal ? `   ·   CVV ${reveal.cvv}` : ''}</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 12 }}>
              <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontFamily: font.semibold }}>{card.holder}</Text>
              <Text style={{ color: 'rgba(255,255,255,.9)', fontSize: 13, fontFamily: font.medium, fontVariant: ['tabular-nums'] }}>{card.expiry}</Text>
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
              { icon: 'plus', label: 'Fund', go: () => setFundOpen(true) },
              { icon: 'lock', label: frozen ? 'Unfreeze' : 'Freeze', go: toggleFreeze },
              { icon: reveal ? 'eyeoff' : 'eye', label: reveal ? 'Hide' : 'Details', go: () => (reveal ? setReveal(null) : (setPinError(''), setDetailsPin(true))) },
            ].map((a) => (
              <Pressable key={a.label} onPress={a.go} style={{ flex: 1 }}>
                <View style={{ alignItems: 'center', gap: 8, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
                  <ZIcon name={a.icon} size={20} color={c.brand} />
                  <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{a.label}</Text>
                </View>
              </Pressable>
            ))}
          </View>

          <Text style={{ paddingHorizontal: 20, marginTop: 18, fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
            Use this card for online & USD payments. Fund it from your wallet; tap Details to reveal the number for a purchase.
          </Text>
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

      {/* Fund: amount sheet -> PIN */}
      <Sheet open={fundOpen} onClose={() => setFundOpen(false)} title="Fund card">
        <QuickAmounts amounts={FUND_AMOUNTS} value={fundAmt} onPick={setFundAmt} />
        <Field value={fundAmt} onChangeText={(v) => setFundAmt(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="Enter amount" prefix={<Text style={{ fontFamily: font.extrabold, color: c.ink2, fontSize: 16 }}>₦</Text>} />
        <View style={{ height: 16 }} />
        <Btn label={Number(fundAmt) > 0 ? `Fund ${money(Number(fundAmt))}` : 'Fund card'} disabled={Number(fundAmt) < 100} onPress={() => { setFundOpen(false); setPinError(''); setTimeout(() => setFundPin(true), 320); }} />
      </Sheet>

      <Sheet open={fundPin} onClose={() => !busy && setFundPin(false)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          {busy ? 'Funding…' : `Load ${money(Number(fundAmt))} onto your card`}
        </Text>
        <PinPad onComplete={(p) => doFund(p)} busy={busy} error={pinError} />
      </Sheet>

      {/* Details reveal: PIN */}
      <Sheet open={detailsPin} onClose={() => !busy && setDetailsPin(false)} title="Reveal card details">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, marginTop: -6, fontFamily: font.regular }}>
          Enter your PIN to show the full card number & CVV
        </Text>
        <PinPad onComplete={(p) => doReveal(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default Cards;

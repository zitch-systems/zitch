import React, { useCallback, useState } from 'react';
import { Alert, View, Text, Pressable, Image } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { useFocusEffect } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { cardsService, type VirtualCard } from '@/lib/services/cards';
import ZIcon from '@/components/design/ZIcon';
import { Screen, Btn, Field, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { QuickAmounts } from '@/components/design/flowkit';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

type Reveal = { pan: string; cvv: string; expiry: string; holder: string };
type CardAction = {
  icon: string;
  label: string;
  color: string;
  go: () => void;
  disabled?: boolean;
};
const FUND_AMOUNTS = [1000, 2000, 5000, 10000, 20000, 50000];

const Cards = () => {
  const { c } = useTheme();
  const { reload: reloadWallet } = useWallet();
  const [card, setCard] = useState<VirtualCard | null>(null);
  const [busy, setBusy] = useState(false);

  // sheets
  const [fundOpen, setFundOpen] = useState(false);
  const [fundAmt, setFundAmt] = useState('');
  const [fundPin, setFundPin] = useState(false);
  const [detailsPin, setDetailsPin] = useState(false);
  const [reveal, setReveal] = useState<Reveal | null>(null);
  const [pinError, setPinError] = useState('');
  const [fundPending, setFundPending] = useState(false);
  const [statusPending, setStatusPending] = useState(false);
  const [issuancePending, setIssuancePending] = useState(false);
  const [issuanceReference, setIssuanceReference] = useState('');

  const load = useCallback(async () => {
    const t = await getToken();
    if (!t) return undefined;
    try {
      const res = await cardsService.list();
      const nextCard = res.cards?.[0] ?? null;
      setCard(nextCard);
      setIssuancePending(Boolean(!nextCard && res.issuance?.pending));
      setIssuanceReference(!nextCard ? String(res.issuance?.reference || '') : '');
      return nextCard;
    } catch {
      return undefined; // keep last state
    }
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const createCard = async () => {
    const fingerprint = 'single';
    let requestKey = '';
    let deliveryStarted = false;
    setBusy(true);
    try {
      requestKey = await acquireSpendAttempt('card-issue', fingerprint);
      deliveryStarted = true;
      const res = await cardsService.create(requestKey);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success' && res.card) {
        await clearSpendAttempt('card-issue', fingerprint, requestKey);
        setCard(res.card);
        setIssuancePending(false);
        setIssuanceReference('');
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setIssuancePending(true);
        setIssuanceReference(String(res.reference || ''));
        notify(
          'Card request under review',
          `Do not create another card. Contact support${res.reference ? ` with reference ${res.reference}` : ''}.`,
          'info',
        );
      } else {
        await clearSpendAttempt('card-issue', fingerprint, requestKey);
        notify('Error', res.message || 'Could not create card');
      }
    } catch {
      if (deliveryStarted) {
        setIssuancePending(true);
        notify(
          'Card request under review',
          'We could not confirm the result. Do not create another card; contact support.',
          'info',
        );
      } else {
        notify('Unable to start card request', 'Could not safely prepare this request. Please try again.');
      }
    }
    finally { setBusy(false); }
  };

  const toggleFreeze = async () => {
    if (!card) return;
    if (card.frozen && card.capabilities.can_unfreeze !== true) {
      notify(
        card.capabilities.permanent_block ? 'Card permanently blocked' : 'Status unavailable',
        card.capabilities.permanent_block
          ? 'This block cannot be reversed. Contact support if you need a replacement card.'
          : 'This card cannot be reactivated in the app.',
        'info',
      );
      return;
    }
    setBusy(true);
    try {
      const res = await cardsService.freeze(card.id);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success' && res.card) {
        setCard(res.card);
        setStatusPending(false);
        notify(
          card.capabilities.permanent_block ? 'Card permanently blocked' : 'Card status updated',
          res.message,
          'success',
        );
      } else if (outcome === 'pending' || outcome === 'unknown') {
        setStatusPending(true);
        await load();
        notify(
          'Card status not confirmed',
          'The request may have completed. Do not try again; reload this page later or contact support.',
          'info',
        );
      } else {
        notify('Unable to update card', res.message || 'The card status was not changed.');
      }
    } catch {
      setStatusPending(true);
      await load();
      notify(
        'Card status not confirmed',
        'The request may have completed. Do not try again; reload this page later or contact support.',
        'info',
      );
    } finally {
      setBusy(false);
    }
  };

  const requestStatusChange = () => {
    if (!card) return;
    if (card.capabilities.permanent_block && !card.frozen) {
      Alert.alert(
        'Permanently block this card?',
        'This action cannot be undone. You will need a replacement card to use card payments again.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Block permanently', style: 'destructive', onPress: () => { void toggleFreeze(); } },
        ],
      );
      return;
    }
    void toggleFreeze();
  };

  const reloadPendingStatus = async () => {
    setBusy(true);
    const latest = await load();
    setBusy(false);
    if (latest?.frozen) {
      setStatusPending(false);
      notify(
        latest.capabilities.permanent_block ? 'Card permanently blocked' : 'Card status updated',
        'The latest card status has been loaded.',
        'info',
      );
    } else {
      notify(
        'Card status still unconfirmed',
        'Do not submit the request again. Please check later or contact support.',
        'info',
      );
    }
  };

  const doFund = async (pin: string) => {
    if (!card) return;
    if (card.capabilities.can_fund !== true) {
      setFundPin(false);
      setFundOpen(false);
      notify('Card funding unavailable', 'This card cannot be topped up after it is created.');
      return;
    }
    const fingerprint = [String(card.id), String(Number(fundAmt))].join('|');
    let requestKey = '';
    let deliveryStarted = false;
    setBusy(true);
    try {
      requestKey = await acquireSpendAttempt('card-fund', fingerprint);
      deliveryStarted = true;
      const res = await cardsService.fund(card.id, fundAmt, pin, requestKey);
      const outcome = classifySpendResponse(res);
      if (outcome === 'success') {
        await clearSpendAttempt('card-fund', fingerprint, requestKey);
        setFundPending(false);
        setFundPin(false);
        setPinError('');
        setFundAmt('');
        reloadWallet();
        await load(); // successful replays contain no `card`; reload authoritative state
        if (isRecoveredSpendResponse(res)) {
          notify(
            'Earlier card funding confirmed',
            'This confirms the earlier attempt; no new card funding was performed. Authorize a new request to add funds again.',
            'info',
          );
        } else {
          notify('Success', 'Card funded');
        }
      }
      else if (outcome === 'pending' || outcome === 'unknown') {
        setFundPending(true);
        setFundPin(false);
        setFundOpen(false);
        setPinError('');
        reloadWallet();
        await load();
        notify(
          'Card funding requires review',
          'We could not confirm this card funding attempt. Check History and contact support before trying again.',
          'info',
        );
      }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else {
        await clearSpendAttempt('card-fund', fingerprint, requestKey);
        setFundPin(false);
        notify('Error', res.message || 'Funding failed');
      }
    } catch {
      if (deliveryStarted) {
        setFundPending(true);
        setFundPin(false);
        setFundOpen(false);
        notify('Card funding requires review', 'Check History and contact support before trying again.', 'info');
      } else {
        notify('Unable to start card funding', 'Could not safely prepare this request. Please try again.');
      }
    }
    finally { setBusy(false); }
  };

  const doReveal = async (pin: string) => {
    if (!card) return;
    setBusy(true);
    try {
      const res = await cardsService.details(card.id, pin);
      if (res.success) {
        setDetailsPin(false);
        setPinError('');
        setReveal({
          pan: String(res.pan || ''),
          cvv: String(res.cvv || ''),
          expiry: String(res.expiry || ''),
          holder: String(res.holder || ''),
        });
      }
      else if (res.code === 'pin_incorrect' || res.code === 'pin_locked') { setPinError(res.message || 'Incorrect PIN'); }
      else { setDetailsPin(false); notify('Error', res.message || 'Could not fetch details'); }
    } catch { setDetailsPin(false); notify('Error', 'Something went wrong.'); }
    finally { setBusy(false); }
  };

  const frozen = card?.frozen ?? false;
  const canFund = card?.capabilities?.can_fund === true;
  const canUnfreeze = card?.capabilities?.can_unfreeze === true;
  const permanentBlock = card?.capabilities?.permanent_block === true;
  const panGroups = reveal?.pan ? String(reveal.pan).replace(/(.{4})/g, '$1 ').trim() : '';
  const cardActions: CardAction[] = [
    ...(canFund ? [{
      icon: 'plus',
      label: fundPending ? 'Funding…' : 'Fund',
      color: '#16A34A',
      go: () => fundPending
        ? notify('Card funding requires review', 'Check History and contact support before starting another funding attempt.', 'info')
        : setFundOpen(true),
    }] : []),
    {
      icon: 'lock',
      label: statusPending
        ? 'Reload status'
        : (permanentBlock ? (frozen ? 'Blocked' : 'Block') : (frozen ? 'Unfreeze' : 'Freeze')),
      color: '#2D7FF9',
      go: statusPending ? () => { void reloadPendingStatus(); } : requestStatusChange,
      disabled: busy || (!statusPending && frozen && !canUnfreeze),
    },
    {
      icon: reveal ? 'eyeoff' : 'eye',
      label: reveal ? 'Hide' : 'Details',
      color: '#7A5CFF',
      go: () => (reveal ? setReveal(null) : (setPinError(''), setDetailsPin(true))),
    },
  ];

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
                <Text style={{ color: '#fff', fontFamily: font.bold, fontSize: 14, letterSpacing: 1.4 }}>
                  {permanentBlock ? 'PERMANENTLY BLOCKED' : '❄ FROZEN'}
                </Text>
              </View>
            )}
          </LinearGradient>

          {/* actions */}
          <View style={{ flexDirection: 'row', gap: 10, marginHorizontal: 16 }}>
            {cardActions.map((a) => (
              <Pressable
                key={a.label}
                accessibilityRole="button"
                accessibilityLabel={a.label}
                onPress={a.go}
                disabled={a.disabled}
                style={{ flex: 1, opacity: a.disabled ? 0.55 : 1 }}
              >
                <View style={{ alignItems: 'center', gap: 8, paddingVertical: 14, borderRadius: 16, backgroundColor: c.surface, borderWidth: 1, borderColor: c.line }}>
                  <View style={{ width: 38, height: 38, borderRadius: 12, backgroundColor: a.color + '22', alignItems: 'center', justifyContent: 'center' }}>
                    <ZIcon name={a.icon} size={20} color={a.color} />
                  </View>
                  <Text style={{ fontSize: 12, fontFamily: font.semibold, color: c.ink2 }}>{a.label}</Text>
                </View>
              </Pressable>
            ))}
          </View>

          <Text style={{ paddingHorizontal: 20, marginTop: 18, fontSize: 12.5, color: c.ink3, fontFamily: font.regular }}>
            {permanentBlock
              ? (frozen
                ? 'This card is permanently blocked and cannot be reactivated. Contact support if you need a replacement.'
                : 'This card cannot be topped up after creation. Blocking it is permanent and cannot be undone.')
              : 'Use this card for online & USD payments. Fund it from your wallet; tap Details to reveal the number for a purchase.'}
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
            {issuancePending
              ? `Your card request is being verified${issuanceReference ? ` (${issuanceReference})` : ''}. Do not submit another request.`
              : 'Create a free virtual card for online & USD payments.'}
          </Text>
          <View style={{ height: 20 }} />
          <Btn
            label={issuancePending ? 'Card request pending' : 'Create a virtual card'}
            icon="plus"
            disabled={busy}
            onPress={issuancePending
              ? () => notify(
                'Card request under review',
                `Contact support${issuanceReference ? ` with reference ${issuanceReference}` : ''}.`,
                'info',
              )
              : createCard}
            full={false}
          />
        </View>
      )}

      {/* Fund: amount sheet -> PIN */}
      <Sheet open={fundOpen} onClose={() => setFundOpen(false)} title="Fund card">
        <QuickAmounts amounts={FUND_AMOUNTS} value={fundAmt} onPick={setFundAmt} />
        <Field value={fundAmt} onChangeText={(v) => setFundAmt(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="Enter amount" prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />} />
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

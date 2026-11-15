import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { pickContactPhone } from '@/lib/contacts';
import { router, useLocalSearchParams } from 'expo-router';
import { apiPost } from '@/lib/api';
import { getSessionGeneration } from '@/lib/secureStore';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse, isRecoveredSpendResponse } from '@/lib/spendOutcome';
import { EP } from '@/lib/endpoints';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, Naira } from '@/components/design/ui';
import { ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { NetworkCircles, NetDot, RecentField, AmountGrid, SectionTitle, NETWORK_LABELS, type RecentItem } from '@/components/design/billkit';
import { useBillRecents, refreshBillRecents, shortDate, groupPhone, networkForPhone } from '@/lib/recents';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { purchasablePhoneNumber } from '@/lib/phone';
import { normalizeAmountInput, MIN_AIRTIME } from '@/lib/amounts';

const NETWORKS = [
  { id: '1', name: 'MTN', color: '#FFCC00', logo: require('@/assets/images/providers/mtn.png') },
  { id: '2', name: 'GLO', color: '#2BB24C', logo: require('@/assets/images/providers/glo.png') },
  { id: '3', name: 'Airtel', color: '#E40000', logo: require('@/assets/images/providers/airtel.png') },
  { id: '4', name: '9mobile', color: '#0A8A3D', logo: require('@/assets/images/providers/9mobile.png') },
];

const AIRTIME_AMOUNTS = [100, 200, 500, 1000, 2000, 5000];

type Step = null | 'confirm' | 'pin';

const BuyAirtime = () => {
  const { c } = useTheme();
  const sessionGeneration = useRef(getSessionGeneration());
  const isCurrentSession = () => sessionGeneration.current === getSessionGeneration();
  const { balance, reload, billPaymentsAvailable, phoneNumber } = useWallet();
  const params = useLocalSearchParams<{ phone?: string }>();
  const [phone, setPhone] = useState(() => purchasablePhoneNumber(params.phone) || purchasablePhoneNumber(phoneNumber));
  const [net, setNet] = useState(() => networkForPhone(phone) || '1');
  const phoneEdited = useRef(false);
  // The network follows the number's prefix until the customer picks one
  // themselves (numbers can be ported, so their choice always wins).
  const netPicked = useRef(false);
  const recents = useBillRecents();
  const own = purchasablePhoneNumber(phoneNumber);
  useEffect(() => {
    if (!phoneEdited.current) setPhone((current) => current || own);
  }, [own]);
  const changePhone = (raw: string) => {
    phoneEdited.current = true;
    const next = purchasablePhoneNumber(raw) || raw.replace(/\D/g, '').slice(0, 15);
    setPhone(next);
    const known = recents.phones.find((r) => r.phone === next)?.network || networkForPhone(next);
    if (known && !netPicked.current) setNet(known);
  };
  useEffect(() => {
    // A number bought for before keeps the network it was bought on.
    const known = recents.phones.find((r) => r.phone === phone)?.network;
    if (known && !netPicked.current) setNet(known);
  }, [recents, phone]);
  const ownNet = recents.phones.find((r) => r.phone === own)?.network || networkForPhone(own);
  const phoneItems: RecentItem[] = [
    ...(own ? [{ value: own, title: groupPhone(own), sub: [NETWORK_LABELS[ownNet], 'My number'].filter(Boolean).join(' · '), leading: <NetDot net={ownNet} size={34} /> }] : []),
    ...recents.phones.filter((r) => r.phone !== own).slice(0, 5).map((r) => ({
      value: r.phone, title: groupPhone(r.phone),
      sub: `${NETWORK_LABELS[r.network] || ''} · Last topped up ${shortDate(r.last_used)}`,
      leading: <NetDot net={r.network} size={34} />,
    })),
  ];
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const purchaseInFlight = useRef(false);
  const reviewedPayment = useRef<string | null>(null);
  const [done, setDone] = useState(false);
  const [pending, setPending] = useState(false);
  const [pendingMessage, setPendingMessage] = useState('');
  const [recovered, setRecovered] = useState(false);
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');

  const network = NETWORKS.find((n) => n.id === net)!;
  const amount = Number(amt || 0);
  const valid = billPaymentsAvailable === true && /^0[789]\d{9}$/.test(phone) && amount >= MIN_AIRTIME && amount <= balance;

  const purchase = async (enteredPin: string) => {
    if (!isCurrentSession() || !valid || done || purchaseInFlight.current) return;
    const fingerprint = [net, phone.trim(), String(amount)].join('|');
    if (reviewedPayment.current !== null && reviewedPayment.current !== JSON.stringify([fingerprint, amount])) {
      setStep('confirm');
      notify('Review payment again', 'The payment details changed. Check them before entering your PIN.');
      return;
    }
    purchaseInFlight.current = true;
    let deliveryStarted = false;
    setBusy(true);
    try {
      const requestKey = await acquireSpendAttempt('airtime', fingerprint);
      if (!isCurrentSession()) return;
      deliveryStarted = true;
      const response = await apiPost(EP.utility.buyAirtime, {
        network: net,
        phone,
        amount: amt,
        transaction_pin: enteredPin,
        idempotency_key: requestKey,
      });
      const result = await response.json();
      if (!isCurrentSession()) return;
      const outcome = classifySpendResponse(result, response.status);
      if (outcome === 'success') {
        await clearSpendAttempt('airtime', fingerprint, requestKey);
        if (!isCurrentSession()) return;
        setRecovered(isRecoveredSpendResponse(result, response.status));
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
        refreshBillRecents();
      } else if (outcome === 'pending' || outcome === 'unknown') {
        // Retain the key: this is still the same unresolved attempt, and the
        // processing receipt prevents another authorization on this screen.
        setPending(true);
        setPendingMessage(outcome === 'pending'
          ? (result.message || 'Your airtime purchase is processing. Its final status will update only after provider confirmation.')
          : 'We could not confirm this airtime purchase. Check History before trying again.');
        setTxnRef(String(result.reference || ''));
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        await clearSpendAttempt('airtime', fingerprint, requestKey);
        if (!isCurrentSession()) return;
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      if (!isCurrentSession()) return;
      if (deliveryStarted) {
        // Delivery is unknown: hold the key and stop a fresh authorization.
        setPending(true);
        setPendingMessage('We could not confirm this airtime purchase. Check History before trying again.');
        setStep(null);
        setDone(true);
        reload();
      } else {
        notify('Unable to start purchase', 'Could not safely prepare this request. Please try again.');
      }
    } finally {
      purchaseInFlight.current = false;
      if (isCurrentSession()) setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Purchase processing' : recovered ? 'Earlier attempt confirmed' : 'Successful'}
          message={pending
            ? pendingMessage
            : recovered
              ? 'This confirms your earlier airtime purchase. No new airtime was bought. Start a new purchase to buy again.'
            : `Your airtime purchase to ${phone} was successful.`}
          rows={[['Type', 'Airtime top-up'], ['Network', network.name], ['Phone', phone], ['Amount', money(amount)], ['Fee', '₦0'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          footer={pending ? <Btn label="Check transaction history" variant="outline" onPress={() => router.push('/history')} /> : undefined}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  // The contacts button on the phone field: asks for permission, then the
  // system picker; only the chosen number is read.
  const pickFromContacts = async () => {
    const res = await pickContactPhone();
    if (res.ok) changePhone(res.contact.phone);
    else if (res.reason === 'no-number') notify('No Nigerian number', 'That contact has no Nigerian mobile number. Pick another contact.');
    else if (res.reason === 'unavailable') notify('Contacts', 'Contacts are not available on this device.');
  };

  return (
    <Screen>
      <Header title="Airtime" onBack={() => router.back()} />

      <SectionTitle>Select network</SectionTitle>
      <NetworkCircles value={net} onPick={(id) => { netPicked.current = true; setNet(id); }} />

      <RecentField
        label="Phone number"
        value={phone}
        onChangeText={changePhone}
        onContacts={pickFromContacts}
        onPick={(it) => changePhone(it.value)}
        items={phoneItems}
        menuTitle="Numbers you've topped up"
        anotherLabel="Use another number"
        tag={phone && phone === own ? 'My number' : undefined}
        leading={<NetDot net={net} size={30} />}
        format={groupPhone}
        maxLength={15}
        placeholder="0801 234 5678"
      />

      <SectionTitle top={22}>Amount</SectionTitle>
      <AmountGrid amounts={AIRTIME_AMOUNTS} value={amt} onPick={setAmt} />
      <View style={{ height: 16 }} />
      <Field
        tint
        label="Or enter amount"
        value={amt}
        onChangeText={(v) => { const normalized = normalizeAmountInput(v); if (normalized !== null) setAmt(normalized); }}
        keyboardType="decimal-pad"
        placeholder="Enter amount"
        prefix={<Naira style={{ color: c.ink2, fontSize: 16, fontWeight: '800' }} />}
      />
      <View style={{ height: 8 }} />
      <BalanceHint amount={amount} balance={balance} min={MIN_AIRTIME} />
      {billPaymentsAvailable !== true ? <Text style={{ color: c.ink3, fontFamily: font.regular, marginBottom: 12 }}>Bill payments are currently unavailable. Refresh your wallet or try again later.</Text> : null}

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm airtime"
        total={amount}
        balance={balance}
        rows={[['Network', network.name], ['Phone', phone], ['Amount', money(amount)]]}
        onPay={() => { reviewedPayment.current = JSON.stringify([[net, phone.trim(), String(amount)].join('|'), amount]); setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet protectScreen open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, lineHeight: 20, color: c.ink3, marginBottom: 16, marginTop: 0, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default BuyAirtime;

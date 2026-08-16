import React, { useEffect, useRef, useState } from 'react';
import { View, Text } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { getToken } from '@/lib/secureStore';
import { apiPost, newIdempotencyKey } from '@/lib/api';
import { Screen, Header, Field, Btn, Sheet, PinPad, money, HeaderLink } from '@/components/design/ui';
import { Label, ProviderGrid, QuickAmounts, QUICK_AMOUNTS, ConfirmSheet, BalanceHint, AmountField } from '@/components/design/flowkit';
import Receipt from '@/components/design/Receipt';
import { notify } from '@/components/design/Notify';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';
import { localPhoneNumber } from '@/lib/phone';

const NETWORKS = [
  { id: '1', name: 'MTN', color: '#FFCC00', logo: require('@/assets/images/providers/mtn.png') },
  { id: '2', name: 'GLO', color: '#2BB24C', logo: require('@/assets/images/providers/glo.png') },
  { id: '3', name: 'Airtel', color: '#E40000', logo: require('@/assets/images/providers/airtel.png') },
  { id: '4', name: '9mobile', color: '#0A8A3D', logo: require('@/assets/images/providers/9mobile.png') },
];

type Step = null | 'confirm' | 'pin';

const BuyAirtime = () => {
  const { c } = useTheme();
  const { balance, reload, phoneNumber } = useWallet();
  const params = useLocalSearchParams<{ phone?: string }>();
  const [, setToken] = useState('');
  const [net, setNet] = useState('1');
  const [phone, setPhone] = useState(params.phone ?? '');
  const [amt, setAmt] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pending, setPending] = useState(false);  // provider-pending: held, confirmed later
  const [pinError, setPinError] = useState('');
  const idemKey = useRef('');  // stable across retries of one purchase attempt
  const phoneSeeded = useRef(false);

  useEffect(() => { getToken().then((t) => t && setToken(t)); }, []);
  useEffect(() => {
    const own = localPhoneNumber(phoneNumber);
    if (!own || phoneSeeded.current) return;
    phoneSeeded.current = true;
    setPhone((current) => current || own);
  }, [phoneNumber]);

  // A change to ANY purchase detail is a new spend — drop the retained key so the
  // next attempt mints a fresh one. The key is kept only across byte-identical
  // retries (so a timed-out-but-delivered attempt replays server-side); reusing
  // it after an edit would replay the PRIOR purchase and render a false receipt
  // for the edited one. Mirrors sendmoney.
  useEffect(() => { idemKey.current = ''; }, [net, phone, amt]);

  const network = NETWORKS.find((n) => n.id === net)!;
  const amount = Number(amt || 0);
  const valid = phone.length >= 10 && amount >= 50 && amount <= balance;

  const purchase = async (enteredPin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const response = await apiPost('/api/utility/buyairtime/', {
        network: net,
        phone,
        amount: amt,
        transaction_pin: enteredPin,
        idempotency_key: idemKey.current,
      });
      const result = await response.json();
      if (response.ok) {
        idemKey.current = '';
        // `pending` = provider timeout: the money is HELD while reconciliation
        // confirms or refunds it — the receipt must say "processing", not claim
        // a delivery that may yet be reversed.
        setTxnRef(String(result.reference || ''));
        setPending(!!result.pending);
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        idemKey.current = '';  // definitive server failure — a retry is a fresh attempt
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
      // network/unknown outcome — keep the key so a retry replays, never double-debits
      notify('Error', 'Something went wrong. Please try again later.');
      setStep(null);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <Screen scroll={false}>
        <Receipt
          title={pending ? 'Processing' : 'Successful'}
          message={pending
            ? `Your airtime purchase to ${phone} is processing and will be confirmed shortly. If it can't be completed, you'll be refunded automatically.`
            : `Your airtime purchase to ${phone} was successful.`}
          rows={[['Type', 'Airtime top-up'], ['Network', network.name], ['Phone', phone], ['Amount', money(amount)], ['Fee', '₦0'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  return (
    <Screen>
      <Header title="Airtime" onBack={() => router.back()} right={<HeaderLink label="History" onPress={() => router.push('/history')} />} />

      <Label>Select network</Label>
      <ProviderGrid items={NETWORKS} value={net} onPick={setNet} />

      <Field
        label="Phone number"
        value={phone}
        onChangeText={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
        keyboardType="number-pad"
        placeholder="0801 234 5678"
      />
      <View style={{ height: 16 }} />

      <Label>Choose amount</Label>
      <QuickAmounts amounts={QUICK_AMOUNTS} value={amt} onPick={setAmt} />
      <AmountField label="Or enter amount" value={amt} onChangeText={setAmt} placeholder="0.00" />
      <View style={{ height: 6 }} />
      <BalanceHint amount={amount} balance={balance} />

      <Btn label="Continue" disabled={!valid} onPress={() => setStep('confirm')} />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm airtime"
        total={amount}
        balance={balance}
        rows={[['Network', network.name], ['Phone', phone], ['Amount', money(amount)]]}
        onPay={() => { setStep(null); setPinError(''); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN" protectScreen>
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} />
      </Sheet>
    </Screen>
  );
};

export default BuyAirtime;

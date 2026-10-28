import React, { useEffect, useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { router } from 'expo-router';
import AuthGuard from '@/components/AuthGuard';
import { Screen, Header, Field, Btn, PinSheet, money } from '@/components/design/ui';
import { ConfirmSheet } from '@/components/design/flowkit';
import { banking, type CatalogItem } from '@/lib/services/banking';
import { apiPost } from '@/lib/api';
import { useWallet } from '@/lib/wallet';
import { useTheme, font } from '@/lib/theme';
import { getSessionGeneration } from '@/lib/secureStore';
import { acquireSpendAttempt, clearSpendAttempt } from '@/lib/pendingSpend';
import { classifySpendResponse } from '@/lib/spendOutcome';
import { normalizeAmountInput } from '@/lib/amounts';

type Kind = 'airtime' | 'data' | 'bill';
type Item = { itemId: string; itemName: string; amount: string | number };
const localPhone = (value: string) => value.replace(/[\s()+-]/g, '').replace(/^234/, '0');
function PaymentScreenContent({ kind = 'bill' }: { kind?: Kind }) {
  const { c } = useTheme();
  const { balance, phoneNumber, billPaymentsAvailable, reload } = useWallet();
  const [phone, setPhone] = useState(localPhone(phoneNumber || ''));
  const [amount, setAmount] = useState('');
  const [categories, setCategories] = useState<CatalogItem[]>([]);
  const [billers, setBillers] = useState<CatalogItem[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [plans, setPlans] = useState<CatalogItem[]>([]);
  const [category, setCategory] = useState('');
  const [biller, setBiller] = useState('');
  const [item, setItem] = useState('');
  const [customer, setCustomer] = useState('');
  const [product, setProduct] = useState('');
  const [validation, setValidation] = useState('');
  const [customerName, setCustomerName] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<'review' | 'pin' | null>(null);
  const [pinError, setPinError] = useState('');
  const [finished, setFinished] = useState(false);
  const [reference, setReference] = useState('');
  const generation = useRef(getSessionGeneration());
  const active = useRef(false);
  const catalogSerial = useRef(0);
  const reviewed = useRef('');
  const current = () => generation.current === getSessionGeneration();
  const fingerprint = JSON.stringify([kind, phone, amount, biller, item, customer, product, validation]);
  // Validation tokens expire. A retry for the same payment must keep its key
  // even after customer validation is renewed.
  const paymentIntent = JSON.stringify([kind, phone, Number(amount).toFixed(2), biller, item, customer.trim(), product]);
  const liveFingerprint = useRef(fingerprint);
  liveFingerprint.current = fingerprint;
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 22 };
  const invalidate = () => { setValidation(''); setCustomerName(''); };
  const fetchCatalog = async (type: string, params: Record<string, string>, consume: (items: CatalogItem[]) => void) => {
    const serial = ++catalogSerial.current;
    setBusy(true);
    try {
      const result = await banking.catalog(type, params);
      if (!current() || serial !== catalogSerial.current) return;
      if (result.success && Array.isArray(result.data)) { consume(result.data); setMessage(''); }
      else setMessage(result.message || 'The bank catalog is unavailable. Please try again.');
    } catch { if (current() && serial === catalogSerial.current) setMessage('Could not load the bank catalog. Please try again.'); }
    finally { if (current() && serial === catalogSerial.current) setBusy(false); }
  };
  useEffect(() => {
    generation.current = getSessionGeneration();
    if (kind === 'bill') void fetchCatalog('categories', {}, setCategories);
    return () => { generation.current = -1; };
  }, [kind]);
  useEffect(() => { if (!phone && phoneNumber) setPhone(localPhone(phoneNumber)); }, [phoneNumber]);
  const validate = async () => {
    if (busy) return;
    const captured = fingerprint;
    setBusy(true);
    try {
      const result = await banking.validate({ biller_id: biller, item_id: item, customer, amount });
      if (!current() || liveFingerprint.current !== captured) return;
      if (result.success && result.validation_reference) { setValidation(result.validation_reference); setCustomerName(result.customer_name || ''); }
      else setMessage(result.message || 'Customer could not be verified.');
    } catch { if (current()) setMessage('Could not verify this customer. Please try again.'); }
    finally { if (current()) setBusy(false); }
  };
  const pay = async (pin: string) => {
    if (!current() || active.current || !valid || reviewed.current !== fingerprint) return;
    active.current = true; setBusy(true); setPinError('');
    const captured = paymentIntent;
    let requested = false;
    try {
      const key = await acquireSpendAttempt('9psb-' + kind, captured);
      if (!current()) return;
      requested = true;
      const response = await apiPost('/api/banking/purchase/', { kind, phone, amount, product_id: product,
        validation_reference: validation, transaction_pin: pin, idempotency_key: key });
      const result = await response.json();
      if (!current()) return;
      const outcome = classifySpendResponse(result, response.status);
      if (outcome === 'success' || outcome === 'failed') {
        await clearSpendAttempt('9psb-' + kind, captured, key);
        if (!current()) return;
      }
      if (outcome === 'success' || outcome === 'pending' || outcome === 'unknown') {
        setFinished(true); setStep(null); setReference(String(result.reference || ''));
        setMessage(outcome === 'success' ? 'Payment completed.' + (result.token ? ` Your token: ${result.token}` : '') : 'Payment is processing. Check transaction history before trying again.');
        await reload();
      } else { setPinError(result.message || 'Payment could not be completed.'); }
    } catch { if (current()) {
      if (requested) { setStep(null); setFinished(true); setMessage('The response was interrupted. Check transaction history; your payment reference is retained for a safe retry.'); }
      else setPinError('Could not safely save this payment request. Please try again.');
    } }
    finally { active.current = false; if (current()) setBusy(false); }
  };
  const numeric = Number(amount);
  const valid = billPaymentsAvailable === true && !busy && !finished && numeric > 0 && numeric <= balance
    && /^0[789][01]\d{8}$/.test(phone) && (kind !== 'data' || !!product) && (kind !== 'bill' || !!validation);
  const title = kind === 'airtime' ? 'Buy airtime' : kind === 'data' ? 'Buy data' : 'Pay bills';
  return <Screen header={<Header title={title} />}><View style={{ gap: 14, paddingBottom: 28 }}>
    <Text style={text}>Available balance: {money(balance)}</Text>
    {billPaymentsAvailable !== true && <Text style={text}>Bill payments are temporarily unavailable.</Text>}
    {!!message && <Text accessibilityLiveRegion="polite" style={text}>{message}</Text>}
    {!finished && <>
      <Field label="Phone number" value={phone} keyboardType="phone-pad" editable={!active.current && !step} onChangeText={(v) => {
        if (active.current) return;
        catalogSerial.current += 1; setBusy(false); setPhone(localPhone(v)); setProduct(''); setPlans([]); invalidate();
      }} />
      {kind === 'data' && <><Btn label="Load data plans for this phone" disabled={busy || phone.length !== 11} onPress={() => void fetchCatalog('data', { phone }, setPlans)} />
        {plans.map((p) => <Btn key={String(p.productId)} variant={product === String(p.productId) ? 'primary' : 'outline'} label={`${p.dataBundle || 'Data plan'} · ${p.validity || ''} · ${money(Number(p.amount))}`} disabled={busy} onPress={() => { setProduct(String(p.productId)); setAmount(String(p.amount)); }} />)}</>}
      {kind === 'bill' && <>
        {!categories.length && <Btn label="Retry loading bill categories" variant="outline" disabled={busy} onPress={() => void fetchCatalog('categories', {}, setCategories)} />}
        {categories.map((entry) => <Btn key={String(entry.id)} label={String(entry.name)} variant={category === String(entry.id) ? 'primary' : 'outline'} disabled={busy} onPress={() => {
          const id = String(entry.id); setCategory(id); setBiller(''); setBillers([]); setItem(''); setItems([]); invalidate();
          void fetchCatalog('billers', { category: id }, setBillers);
        }} />)}
        {billers.map((entry) => <Btn key={String(entry.id)} label={String(entry.name)} variant={biller === String(entry.id) ? 'primary' : 'outline'} disabled={busy} onPress={() => {
          const id = String(entry.id); setBiller(id); setItem(''); setItems([]); invalidate();
          void fetchCatalog('fields', { biller: id }, (fields) => setItems(fields.flatMap((field) => field.items || [])));
        }} />)}
        {items.map((entry) => <Btn key={String(entry.itemId)} label={`${entry.itemName}${Number(entry.amount) > 0 ? ' · ' + money(Number(entry.amount)) : ''}`} variant={item === String(entry.itemId) ? 'primary' : 'outline'} disabled={busy} onPress={() => { setItem(String(entry.itemId)); setAmount(Number(entry.amount) > 0 ? String(entry.amount) : ''); invalidate(); }} />)}
        <Field label="Customer, meter or subscription number" value={customer} editable={!busy} onChangeText={(v) => { setCustomer(v); invalidate(); }} />
      </>}
      <Field label="Amount (NGN)" value={amount} keyboardType="decimal-pad" editable={!busy && kind !== 'data' && !(kind === 'bill' && Number(items.find((entry) => String(entry.itemId) === item)?.amount) > 0)} onChangeText={(v) => { const next = normalizeAmountInput(v); if (next !== null) { setAmount(next); invalidate(); } }} />
      {kind === 'bill' && <><Btn label="Verify customer" disabled={busy || !biller || !item || !customer || numeric <= 0} onPress={() => void validate()} />{!!customerName && <Text style={text}>Customer: {customerName}</Text>}</>}
      <Btn label="Review payment" disabled={!valid} onPress={() => { reviewed.current = fingerprint; setStep('review'); }} />
    </>}
    {finished && <Btn label="View transaction history" variant="outline" onPress={() => router.push('/(homepage)/history')} />}
    {!!reference && <Text selectable style={text}>Reference: {reference}</Text>}
    <ConfirmSheet open={step === 'review'} onClose={() => setStep(null)} title={title} total={numeric} balance={balance}
      rows={[[kind === 'bill' ? 'Customer' : 'Phone', kind === 'bill' ? customerName : phone], ['Amount', money(numeric)]]}
      onPay={() => { if (valid && reviewed.current === fingerprint) { setStep('pin'); setPinError(''); } }} />
    <PinSheet open={step === 'pin'} busy={busy} error={pinError} onClose={() => { if (!busy) setStep(null); }} onComplete={(pin) => void pay(pin)} />
  </View></Screen>;
}

export default function PaymentScreen({ kind = 'bill' }: { kind?: Kind }) {
  return <AuthGuard><PaymentScreenContent kind={kind} /></AuthGuard>;
}

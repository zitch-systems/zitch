import React, { useCallback, useEffect, useRef, useState } from 'react';
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
const localPhone = (value: string) => value.replace(/[\s()+-]/g, '').replace(/^234/, '0');
function PaymentScreenContent({ kind = 'bill' }: { kind?: Kind }) {
  const { c } = useTheme();
  const { balance, phoneNumber, billPaymentsAvailable, reload } = useWallet();
  const [phone, setPhone] = useState(localPhone(phoneNumber || ''));
  const [amount, setAmount] = useState('');
  const [categories, setCategories] = useState<CatalogItem[]>([]);
  const [billers, setBillers] = useState<CatalogItem[]>([]);
  const [fields, setFields] = useState<CatalogItem[]>([]);
  const [fieldsReady, setFieldsReady] = useState(false);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const items = fields.flatMap((field) => field.isSelectData === 'Y' ? field.items || [] : []);
  const extraFields = fields.filter((field) => field.validation === 'Y' && ['firstname', 'lastname'].includes(field.fieldName || ''));
  const hasSelection = fields.some((field) => field.fieldName === 'itemId');
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
  const [paymentStatus, setPaymentStatus] = useState('Pending');
  const [attempt, setAttempt] = useState<{ fingerprint: string; key: string } | null>(null);
  const generation = useRef(getSessionGeneration());
  const active = useRef(false);
  const validating = useRef(false);
  const catalogSerial = useRef(0);
  const reviewed = useRef('');
  const current = useCallback(() => generation.current === getSessionGeneration(), []);
  const fingerprint = JSON.stringify([kind, phone, amount, biller, item, customer, product, validation, inputs]);
  const validationIntent = JSON.stringify([phone, amount, biller, item, customer, inputs]);
  const liveValidationIntent = useRef(validationIntent);
  liveValidationIntent.current = validationIntent;
  // Validation tokens expire. A retry for the same payment must keep its key
  // even after customer validation is renewed.
  const paymentIntent = JSON.stringify([kind, phone, Number(amount).toFixed(2), biller, item, customer.trim(), product]);
  const text = { color: c.ink1, fontFamily: font.regular, lineHeight: 22 };
  const invalidate = () => { setValidation(''); setCustomerName(''); };
  const resetFields = () => { setFields([]); setFieldsReady(false); setInputs({}); setItem(''); setAmount(''); setCustomer(''); invalidate(); };
  const fetchCatalog = useCallback(async (type: string, params: Record<string, string>, consume: (items: CatalogItem[]) => void) => {
    const serial = ++catalogSerial.current;
    setBusy(true);
    try {
      const result = await banking.catalog(type, params);
      if (!current() || serial !== catalogSerial.current) return;
      if (result.success && Array.isArray(result.data)) { consume(result.data); setMessage(''); }
      else setMessage(result.message || 'The bank catalog is unavailable. Please try again.');
    } catch { if (current() && serial === catalogSerial.current) setMessage('Could not load the bank catalog. Please try again.'); }
    finally { if (current() && serial === catalogSerial.current) setBusy(false); }
  }, [current]);
  useEffect(() => {
    generation.current = getSessionGeneration();
    if (kind === 'bill' && billPaymentsAvailable === true) void fetchCatalog('categories', {}, setCategories);
    return () => { generation.current = -1; catalogSerial.current += 1; };
  }, [kind, billPaymentsAvailable, fetchCatalog]);
  useEffect(() => { if (phoneNumber) setPhone((prior) => prior || localPhone(phoneNumber)); }, [phoneNumber]);
  const validate = async () => {
    if (!current() || busy || validating.current) return;
    const captured = validationIntent;
    validating.current = true; setBusy(true); invalidate();
    try {
      const result = await banking.validate({ biller_id: biller, item_id: item, customer, amount, inputs });
      if (!current() || liveValidationIntent.current !== captured) return;
      if (result.success && result.validation_reference && Number(result.amount) === Number(amount)) {
        setValidation(result.validation_reference); setCustomerName(result.customer_name || ''); setMessage('');
      }
      else setMessage(result.message || 'Customer could not be verified.');
    } catch { if (current()) setMessage('Could not verify this customer. Please try again.'); }
    finally { validating.current = false; if (current()) setBusy(false); }
  };
  const pay = async (pin: string) => {
    if (!current() || active.current || !valid || reviewed.current !== fingerprint) return;
    active.current = true; setBusy(true); setPinError('');
    const captured = paymentIntent;
    let requested = false;
    try {
      const key = await acquireSpendAttempt('9psb-' + kind, captured);
      if (!current()) return;
      setAttempt({ fingerprint: captured, key });
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
        setPaymentStatus(outcome === 'success' ? 'Successful' : 'Pending');
        setMessage(outcome === 'success' ? 'Payment completed.' + (result.token ? ` Your token: ${result.token}` : result.receipt_pending ? ' Your token is being retrieved. Check transaction history shortly.' : '') + (result.electricity_units ? ` Units: ${result.electricity_units}` : '') : 'Payment is processing. Check transaction history before trying again.');
        // A wallet refresh failure cannot replace a confirmed purchase receipt.
        try { await reload(); } catch { /* Preserve the confirmed result. */ }
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
          const id = String(entry.id); setCategory(id); setBiller(''); setBillers([]); resetFields();
          void fetchCatalog('billers', { category: id }, setBillers);
        }} />)}
        {billers.map((entry) => <Btn key={String(entry.id)} label={String(entry.name)} variant={biller === String(entry.id) ? 'primary' : 'outline'} disabled={busy} onPress={() => {
          const id = String(entry.id); setBiller(id); resetFields();
          void fetchCatalog('fields', { biller: id }, (rows) => {
            setFields(rows); setFieldsReady(rows.some((field) => field.fieldName === 'customerId'));
          });
        }} />)}
        {items.map((entry) => <Btn key={String(entry.itemId)} label={`${entry.itemName}${Number(entry.amount) > 0 ? ' · ' + money(Number(entry.amount)) : ''}`} variant={item === String(entry.itemId) ? 'primary' : 'outline'} disabled={busy} onPress={() => { setItem(String(entry.itemId)); setAmount(Number(entry.amount) > 0 ? String(entry.amount) : ''); invalidate(); }} />)}
        <Field label="Customer, meter or subscription number" value={customer} editable={!busy} onChangeText={(v) => { setCustomer(v); invalidate(); }} />
        {extraFields.map((field) => <Field key={field.fieldName} label={field.fieldName === 'firstname' ? 'Customer first name' : 'Customer last name'}
          placeholder={field.fieldDescription} value={inputs[field.fieldName!] || ''} editable={!busy} maxLength={100}
          onChangeText={(value) => { setInputs((prior) => ({ ...prior, [field.fieldName!]: value })); invalidate(); }} />)}
      </>}
      <Field label="Amount (NGN)" value={amount} keyboardType="decimal-pad" editable={!busy && kind !== 'data' && !(kind === 'bill' && Number(items.find((entry) => String(entry.itemId) === item)?.amount) > 0)} onChangeText={(v) => { const next = normalizeAmountInput(v); if (next !== null) { setAmount(next); invalidate(); } }} />
      {kind === 'bill' && <><Btn label="Verify customer" disabled={busy || !biller || !fieldsReady || (hasSelection && !item) || !customer.trim() || numeric <= 0 || extraFields.some((field) => !inputs[field.fieldName!]?.trim())} onPress={() => void validate()} />{!!customerName && <Text style={text}>Customer: {customerName}</Text>}</>}
      <Btn label="Review payment" disabled={!valid} onPress={() => { reviewed.current = fingerprint; setStep('review'); }} />
    </>}
    {finished && !!reference && attempt && <Btn label="View payment status" onPress={() => router.push({
      pathname: '/(homepage)/txndetail', params: { reference, status: paymentStatus,
        spendScope: '9psb-' + kind, spendFingerprint: attempt.fingerprint, spendKey: attempt.key },
    })} />}
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

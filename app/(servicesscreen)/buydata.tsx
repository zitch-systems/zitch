import React, { useEffect, useMemo, useRef, useState } from 'react';
import { View, Text, Pressable, Image } from 'react-native';
import { Loading } from '@/components/design/Loading';
import { router } from 'expo-router';
import { apiPost, newIdempotencyKey, publicJson } from '@/lib/api';
import {
  Screen, Header, Btn, Sheet, PinPad, money, HeaderLink, PillTabs, PickerSheet, Card, NText,
} from '@/components/design/ui';
import ZIcon from '@/components/design/ZIcon';
import { ConfirmSheet, BalanceHint } from '@/components/design/flowkit';
import { notify } from '@/components/design/Notify';
import Receipt from '@/components/design/Receipt';
import { useTheme, font } from '@/lib/theme';
import { useWallet } from '@/lib/wallet';

const NETWORKS = [
  { id: '1', name: 'MTN', color: '#FFCC00', logo: require('@/assets/images/providers/mtn.png') },
  { id: '2', name: 'GLO', color: '#2BB24C', logo: require('@/assets/images/providers/glo.png') },
  { id: '3', name: 'Airtel', color: '#E40000', logo: require('@/assets/images/providers/airtel.png') },
  { id: '4', name: '9mobile', color: '#0A8A3D', logo: require('@/assets/images/providers/9mobile.png') },
];

// A real API parameter — it selects which catalogue the provider serves, so it
// stays a control rather than becoming a display category.
const PLAN_TYPES = [
  { v: '1', label: 'SME' },
  { v: '2', label: 'SME2' },
  { v: '3', label: 'Gifting' },
  { v: '4', label: 'Corporate' },
];

const TABS = [
  { v: 'all', label: 'All' },
  { v: 'daily', label: 'Daily' },
  { v: 'weekly', label: 'Weekly' },
  { v: 'monthly', label: 'Monthly' },
  { v: 'long', label: 'Broadband' },
];

// Derived from the plan's own validity string rather than a field the API
// doesn't send. Anything that doesn't parse stays visible under "All" instead of
// being filed under a guess.
const planCategory = (validity: string, name: string): string => {
  const s = `${validity} ${name}`.toLowerCase();
  const days = /(\d+)\s*day/.exec(s);
  if (/broadband|router|mifi/.test(s)) return 'long';
  if (days) {
    const n = Number(days[1]);
    if (n <= 3) return 'daily';
    if (n <= 14) return 'weekly';
    if (n <= 31) return 'monthly';
    return 'long';
  }
  if (/month/.test(s)) return /(\d+)\s*month/.test(s) && Number(/(\d+)\s*month/.exec(s)![1]) > 1 ? 'long' : 'monthly';
  if (/week/.test(s)) return 'weekly';
  if (/day|daily/.test(s)) return 'daily';
  if (/year|annual/.test(s)) return 'long';
  return '';
};

/** "0816 693 8327" — grouped while typing so a mistyped digit is spottable. */
const prettyPhone = (d: string) =>
  d.replace(/(\d{4})(\d{0,3})(\d{0,4})/, (_, a, b, cc) => [a, b, cc].filter(Boolean).join(' ')).trim();

type Step = null | 'confirm' | 'pin';
type Plan = { id: string; label: string; sub?: string; price: number };

const BuyData = () => {
  const { c } = useTheme();
  const { balance, reload, txns } = useWallet();
  const [net, setNet] = useState('1');
  const [planType, setPlanType] = useState('1');
  const [plan, setPlan] = useState('');
  const [phone, setPhone] = useState('');
  const [price, setPrice] = useState('');
  const [plans, setPlans] = useState<Plan[]>([]);
  const [loadingPlans, setLoadingPlans] = useState(false);
  const [tab, setTab] = useState('all');
  const [grid, setGrid] = useState(true);
  const [picker, setPicker] = useState<null | 'net' | 'type' | 'phone'>(null);
  const [phoneDraft, setPhoneDraft] = useState('');
  const [step, setStep] = useState<Step>(null);
  const [pinFirst, setPinFirst] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  // The ledger reference the server minted for this transaction — shown on the
  // receipt and carried into the saved/shared file, so a support ticket can name it.
  const [txnRef, setTxnRef] = useState('');
  const [pinError, setPinError] = useState('');

  // Fetch plans whenever network + plan type are chosen.
  useEffect(() => {
    if (!net || !planType) return;
    let active = true;
    const timer = setTimeout(() => {
      if (!active) return;
      setLoadingPlans(true);
      setPlan('');
      setPlans([]);
      publicJson('/api/utility/get_data_plans/', { datanetwork: net, selectedPlanType: planType })
      .then((res) => {
        if (active && res?.data_plans) {
          setPlans(res.data_plans.map((p: any) => ({
            id: String(p.plan_code),
            label: p.name,
            sub: p.validity,
            price: Number(p.price ?? 0),
          })));
        }
      })
      .catch(() => {})
      .finally(() => { if (active) setLoadingPlans(false); });
    }, 0);
    return () => { active = false; clearTimeout(timer); };
  }, [net, planType]);

  // Fetch authoritative price for the chosen plan.
  useEffect(() => {
    let active = true;
    const timer = setTimeout(() => {
      if (!active) return;
      if (!plan) { setPrice(''); return; }
      publicJson('/api/utility/get_data_plans_price/', { selectedDataPlan: plan })
      .then((res) => { if (active && res?.price != null) setPrice(String(res.price)); })
      .catch(() => {});
    }, 0);
    return () => { active = false; clearTimeout(timer); };
  }, [plan]);

  const network = NETWORKS.find((n) => n.id === net)!;
  const planObj = plans.find((p) => p.id === plan);
  const amount = Number(price || planObj?.price || 0);
  const valid = phone.length >= 10 && !!plan && amount > 0 && amount <= balance;
  const [pending, setPending] = useState(false);  // provider-pending: held, confirmed later
  const idemKey = useRef('');  // stable across retries of one purchase attempt

  // Any edit to the purchase details is a new spend — drop the retained key so a
  // stale one can't replay the PRIOR purchase for the edited one (mirrors sendmoney).
  useEffect(() => { idemKey.current = ''; }, [net, planType, plan, phone]);

  // Which tabs to show: only the ones this catalogue actually has plans for. A
  // tab that opens onto "no plans" is a dead end the customer paid attention to.
  const { shown, tabs, activeTab } = useMemo(() => {
    const cats = new Set(plans.map((p) => planCategory(p.sub ?? '', p.label)).filter(Boolean));
    const list = TABS.filter((t) => t.v === 'all' || cats.has(t.v));
    // A tab that was live for MTN may not exist for Airtel. Falling back HERE,
    // rather than in an effect that re-sets state, means the grid never renders
    // an empty frame under a selected-but-absent tab.
    const active = list.some((t) => t.v === tab) ? tab : 'all';
    return {
      tabs: list,
      activeTab: active,
      shown: active === 'all' ? plans : plans.filter((p) => planCategory(p.sub ?? '', p.label) === active),
    };
  }, [plans, tab]);

  // Numbers this customer has actually topped up before, newest first. Real
  // history, not the phone's address book — the app holds no contacts permission.
  const recentNumbers = useMemo(() => {
    const seen: string[] = [];
    for (const t of txns) {
      const m = /\b(0\d{10})\b/.exec(t.detail || '');
      if (m && !seen.includes(m[1])) seen.push(m[1]);
      if (seen.length >= 6) break;
    }
    return seen;
  }, [txns]);

  const purchase = async (enteredPin: string) => {
    if (!idemKey.current) idemKey.current = newIdempotencyKey();
    setBusy(true);
    try {
      const response = await apiPost('/api/utility/buydata/', {
        phone,
        datanetwork: net,
        selectedDataPlan: plan,
        transaction_pin: enteredPin,
        idempotency_key: idemKey.current,
      });
      const result = await response.json();
      if (response.ok) {
        idemKey.current = '';
        // `pending` = provider timeout: held while reconciliation confirms or
        // refunds — the receipt must say "processing", not claim delivery.
        setTxnRef(String(result.reference || ''));
        setPending(!!result.pending);
        setStep(null);
        setDone(true);
        reload();
      } else if (result.code === 'pin_incorrect' || result.code === 'pin_locked') {
        setPinError(result.message || 'Incorrect PIN');  // keep key: no debit happened
      } else {
        idemKey.current = '';  // definitive server failure — retry is a fresh attempt
        notify('Error', result.message || 'Transaction failed');
        setStep(null);
      }
    } catch {
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
            ? `Your ${planObj?.label || 'data'} purchase to ${phone} is processing and will be confirmed shortly. If it can't be completed, you'll be refunded automatically.`
            : `Your ${planObj?.label || 'data'} purchase to ${phone} was successful.`}
          rows={[['Type', 'Data bundle'], ['Network', network.name], ['Phone', phone], ['Plan', planObj?.label || '—'], ['Total', money(amount), true]]}
          reference={txnRef}
          status={pending ? 'Processing' : 'Successful'}
          onDone={() => router.replace('/home')}
        />
      </Screen>
    );
  }

  const PlanCard = ({ p }: { p: Plan }) => {
    const on = p.id === plan;
    return (
      <Pressable
        onPress={() => setPlan(p.id)}
        accessibilityRole="radio"
        accessibilityLabel={`${p.label}, ${p.sub ?? ''}, ${money(p.price)}`}
        accessibilityState={{ selected: on }}
        style={{
          width: grid ? '33.333%' : '100%',
          padding: 5,
        }}
      >
        <View
          style={{
            borderRadius: 16,
            backgroundColor: on ? 'transparent' : c.surface2,
            borderWidth: 2,
            borderColor: on ? c.brand : 'transparent',
            paddingVertical: grid ? 16 : 14,
            paddingHorizontal: grid ? 8 : 16,
            alignItems: grid ? 'center' : undefined,
            flexDirection: grid ? 'column' : 'row',
            gap: grid ? 0 : 12,
          }}
        >
          <View style={{ flex: grid ? undefined : 1, alignItems: grid ? 'center' : 'flex-start' }}>
            <Text style={{ fontSize: grid ? 19 : 16, fontFamily: font.extrabold, color: c.ink1, letterSpacing: -0.3 }}>
              {p.label}
            </Text>
            {p.sub ? (
              <Text style={{ fontSize: 12, fontFamily: font.regular, color: c.ink3, marginTop: 3 }}>{p.sub}</Text>
            ) : null}
          </View>
          <NText style={{ fontSize: grid ? 14 : 16, fontFamily: font.bold, color: on ? c.brand : c.ink2, marginTop: grid ? 8 : 0, fontVariant: ['tabular-nums'] }}>
            ₦{p.price.toLocaleString()}
          </NText>
        </View>
      </Pressable>
    );
  };

  return (
    <Screen>
      <Header
        title="Mobile Data"
        onBack={() => router.back()}
        right={<HeaderLink label="History" onPress={() => router.push('/history')} />}
      />

      {/* --- who to top up --- */}
      <Card style={{ marginBottom: 14 }} pad={14}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <Pressable
            onPress={() => setPicker('net')}
            accessibilityRole="button"
            accessibilityLabel={`Network, ${network.name}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}
          >
            <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: '#fff', borderWidth: 1, borderColor: c.line, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
              <Image source={network.logo} resizeMode="contain" style={{ width: 28, height: 28 }} />
            </View>
            <ZIcon name="down" size={14} color={c.ink3} stroke={2.4} />
          </Pressable>
          <View style={{ width: 1, height: 28, backgroundColor: c.line }} />
          <Text
            style={{ flex: 1, fontSize: 17, fontFamily: font.bold, color: phone ? c.ink1 : c.ink3, letterSpacing: 0.2 }}
            numberOfLines={1}
            onPress={() => { setPhoneDraft(phone); setPicker('phone'); }}
            suppressHighlighting
            accessibilityRole="button"
            accessibilityLabel={phone ? `Phone number ${phone}` : 'Enter phone number'}
          >
            {phone ? prettyPhone(phone) : 'Enter phone number'}
          </Text>
          <Pressable
            onPress={() => { setPhoneDraft(phone); setPicker('phone'); }}
            accessibilityRole="button"
            accessibilityLabel="Choose a saved number"
            hitSlop={8}
            style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: c.brand, alignItems: 'center', justifyContent: 'center' }}
          >
            <ZIcon name="contacts" size={18} color={c.inkOnBrand} stroke={2} />
          </Pressable>
        </View>
      </Card>

      {/* --- plans --- */}
      <Card style={{ marginBottom: 16 }} pad={14}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 12 }}>
          <Text style={{ flex: 1, fontSize: 16, fontFamily: font.bold, color: c.ink1 }}>Data Plans</Text>
          <Pressable
            onPress={() => setPicker('type')}
            accessibilityRole="button"
            accessibilityLabel={`Plan catalogue, ${PLAN_TYPES.find((t) => t.v === planType)?.label}`}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 11, paddingVertical: 6, borderRadius: 999, backgroundColor: c.surface3 }}
          >
            <Text style={{ fontSize: 12.5, fontFamily: font.bold, color: c.ink2 }}>
              {PLAN_TYPES.find((t) => t.v === planType)?.label}
            </Text>
            <ZIcon name="down" size={13} color={c.ink3} stroke={2.4} />
          </Pressable>
          <Pressable
            onPress={() => setGrid((g) => !g)}
            accessibilityRole="button"
            accessibilityLabel={grid ? 'Show plans as a list' : 'Show plans as a grid'}
            hitSlop={8}
          >
            <ZIcon name={grid ? 'more' : 'qr'} size={20} color={c.brand} />
          </Pressable>
        </View>

        <PillTabs options={tabs} value={activeTab} onChange={setTab} />
        <View style={{ height: 12 }} />

        {loadingPlans ? (
          <Loading full={false} />
        ) : shown.length === 0 ? (
          <Text style={{ color: c.ink3, fontFamily: font.regular, textAlign: 'center', paddingVertical: 24 }}>
            {plans.length === 0 ? 'No plans available for this selection.' : 'No plans in this category.'}
          </Text>
        ) : (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -5 }}>
            {shown.map((p) => <PlanCard key={p.id} p={p} />)}
          </View>
        )}
      </Card>

      <BalanceHint amount={amount} balance={balance} />
      <Btn label={amount > 0 ? `Continue · ${money(amount)}` : 'Continue'} disabled={!valid} onPress={() => setStep('confirm')} />

      <PickerSheet
        open={picker === 'net'}
        onClose={() => setPicker(null)}
        title="Select network"
        options={NETWORKS.map((n) => ({ v: n.id, label: n.name }))}
        value={net}
        onPick={setNet}
      />
      <PickerSheet
        open={picker === 'type'}
        onClose={() => setPicker(null)}
        title="Plan catalogue"
        options={PLAN_TYPES.map((t) => ({ v: t.v, label: t.label }))}
        value={planType}
        onPick={setPlanType}
      />

      <PhoneSheet
        open={picker === 'phone'}
        onClose={() => setPicker(null)}
        draft={phoneDraft}
        setDraft={setPhoneDraft}
        onChange={setPhone}
        recents={recentNumbers}
      />

      <ConfirmSheet
        open={step === 'confirm'}
        onClose={() => setStep(null)}
        title="Confirm data"
        total={amount}
        balance={balance}
        productIcon={network.logo}
        rows={[['Product', `${network.name} Mobile Data`], ['Recipient Mobile', prettyPhone(phone)], ['Data Bundle', `${planObj?.label ?? '—'}${planObj?.sub ? ` · ${planObj.sub}` : ''}`]]}
        onPay={(pinOnly) => { setStep(null); setPinError(''); setPinFirst(!!pinOnly); setTimeout(() => setStep('pin'), 320); }}
      />

      <Sheet open={step === 'pin'} onClose={() => !busy && setStep(null)} title="Enter your PIN">
        <Text style={{ fontSize: 13.5, color: c.ink3, marginBottom: 18, fontFamily: font.regular }}>
          {busy ? 'Authorizing payment…' : `Confirm payment of ${money(amount)}`}
        </Text>
        {/* Asked for the PIN explicitly on the sheet, so don't open the biometric
            prompt over the pad they just chose. */}
        <PinPad onComplete={(p) => purchase(p)} busy={busy} error={pinError} autoBiometric={!pinFirst} />
      </Sheet>
    </Screen>
  );
};

/** Number entry + the numbers this customer has topped up before. The app holds
 *  no contacts permission, so this offers real history rather than the phone's
 *  address book — everything it lists is a number the customer has actually used. */
const PhoneSheet = ({
  open, onClose, draft, setDraft, onChange, recents,
}: {
  open: boolean; onClose: () => void; draft: string;
  setDraft: React.Dispatch<React.SetStateAction<string>>;
  onChange: (v: string) => void; recents: string[];
}) => {
  const { c } = useTheme();
  // The draft lives in the parent and is seeded when the sheet is OPENED, not
  // synced back here by an effect — an effect that re-set state on every render
  // of an open sheet would fight the keypad it is meant to serve.
  const ok = draft.length >= 10;
  return (
    <Sheet open={open} onClose={onClose} title="Phone number">
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, height: 58, borderRadius: 16, backgroundColor: c.surface2, borderWidth: 1.5, borderColor: c.line, paddingHorizontal: 16, marginBottom: 18 }}>
        <ZIcon name="phone" size={19} color={c.ink3} />
        <Text
          style={{ flex: 1, fontSize: 17, fontFamily: font.bold, color: draft ? c.ink1 : c.ink3 }}
        >
          {draft ? prettyPhone(draft) : '0801 234 5678'}
        </Text>
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -4, marginBottom: 18 }}>
        {['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'].map((k, i) => (
          <View key={i} style={{ width: '33.333%', padding: 4 }}>
            {k ? (
              <Pressable
                onPress={() => setDraft((d) => (k === '⌫' ? d.slice(0, -1) : (d + k).slice(0, 11)))}
                accessibilityRole="button"
                accessibilityLabel={k === '⌫' ? 'Delete' : k}
                style={({ pressed }) => ({ height: 54, borderRadius: 14, backgroundColor: pressed ? c.surface3 : c.surface2, alignItems: 'center', justifyContent: 'center' })}
              >
                <Text style={{ fontSize: 21, fontFamily: font.bold, color: c.ink1 }}>{k}</Text>
              </Pressable>
            ) : <View style={{ height: 54 }} />}
          </View>
        ))}
      </View>
      {recents.length > 0 && (
        <>
          <Text style={{ fontSize: 13, fontFamily: font.bold, color: c.ink2, marginBottom: 10 }}>
            Numbers you&apos;ve topped up
          </Text>
          {recents.map((n) => (
            <Pressable
              key={n}
              onPress={() => { onChange(n); onClose(); }}
              accessibilityRole="button"
              accessibilityLabel={`Use ${n}`}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: c.line }}
            >
              <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: c.surface3, alignItems: 'center', justifyContent: 'center' }}>
                <ZIcon name="phone" size={17} color={c.ink2} />
              </View>
              <Text style={{ flex: 1, fontSize: 15, fontFamily: font.semibold, color: c.ink1 }}>{prettyPhone(n)}</Text>
              <ZIcon name="right" size={16} color={c.ink3} />
            </Pressable>
          ))}
          <View style={{ height: 18 }} />
        </>
      )}
      <Btn
        label="Use this number"
        disabled={!ok}
        onPress={() => { onChange(draft); onClose(); }}
      />
    </Sheet>
  );
};

export default BuyData;

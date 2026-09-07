// flows.jsx — Zitch service flows (airtime, data, cable, betting, exams, electricity, transfer, loan, add money)
(function () {
  const { useState } = React;
  const D = window.ZDATA;
  const { fmtN, fmtK } = window.ZUI;
  const { useApp, AppHeader, PrimaryButton, BottomBar, Field, Segmented, QuickAmounts, ProviderGrid, PlanList, ListRow, Monogram, ConfirmSheet, PinSheet, OptionSheet, Sheet, BiometricScan, Toggle, Tap } = window;
  const I = (props) => React.createElement(window.ZIcon, props);
  const SB = () => React.createElement(window.StatusBar, null);

  function Screen({ title, sub, onBack, right, children, footer }) {
    const app = useApp();
    return (
      <div className="z-screen" style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-grad)' }}>
        <SB />
        <div style={{ maxWidth: app.wide ? 600 : 'none', width: '100%', margin: '0 auto' }}><AppHeader title={title} sub={sub} onBack={onBack} right={right} /></div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '4px 18px 130px' }}>
          <div style={{ maxWidth: app.wide ? 564 : 'none', margin: '0 auto' }}>{children}</div>
        </div>
        {footer && <BottomBar>{footer}</BottomBar>}
      </div>
    );
  }
  window.Screen = Screen;

  function Label({ children }) { return <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink-1)', margin: '6px 0 12px' }}>{children}</div>; }

  // balance shown to the right, below an amount input; warns when amount exceeds balance
  function BalanceHint({ amount }) {
    const app = useApp();
    const short = amount > 0 && amount > app.balance;
    if (short) return (
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: -6, marginBottom: 14 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--z-red)' }}>Insufficient balance</span>
        <Tap onClick={() => app.nav.push('addmoney')}><span style={{ fontSize: 12, fontWeight: 700, color: 'var(--brand)' }}>+ Add money</span></Tap>
      </div>
    );
    return <div style={{ textAlign: 'right', fontSize: 12, color: 'var(--ink-3)', marginTop: -6, marginBottom: 14 }}>Balance: <span className="z-num" style={{ fontWeight: 700, color: 'var(--ink-2)' }}>{fmtN(app.balance)}</span></div>;
  }

  // checkout hook: manages confirm + pin sheets
  function useCheckout() {
    const [step, setStep] = useState(null);
    return { step, setStep };
  }
  function Sheets({ step, setStep, confirm, amount, finish }) {
    const app = useApp();
    return (<>
      {step === 'confirm' && <ConfirmSheet {...confirm} total={amount} onPay={() => setStep(app.biometrics ? 'bio' : 'pin')} onClose={() => setStep(null)} />}
      {step === 'bio' && <BiometricScan title="Approve payment" subtitle={'Authorize ' + window.ZUI.fmtN(amount)} faceMode={app.faceMode} onDone={finish} onFallback={() => setStep('pin')} onClose={() => setStep(null)} />}
      {step === 'pin' && <PinSheet amount={amount} onDone={finish} onClose={() => setStep(null)} onBio={app.biometrics ? () => setStep('bio') : null} />}
    </>);
  }

  // ============ AIRTIME & DATA ============
  function AirtimeData({ initialTab, initialPhone }) {
    const app = useApp();
    const [tab, setTab] = useState(initialTab || 'airtime');
    const [net, setNet] = useState('mtn');
    const [phone, setPhone] = useState(initialPhone || '08145872210');
    const [amt, setAmt] = useState('');
    const [plan, setPlan] = useState(null);
    const { step, setStep } = useCheckout();
    const network = D.NETWORKS.find(n => n.id === net);
    const planObj = (D.DATA_PLANS[net] || []).find(p => p.id === plan);
    const amount = tab === 'airtime' ? Number(amt || 0) : (planObj ? planObj.price : 0);
    const valid = phone.length >= 10 && amount > 0 && amount <= app.balance;
    const finish = () => {
      app.pay(amount, { mono: network.name.slice(0, 2).toUpperCase(), t: (tab === 'airtime' ? 'Airtime — ' : 'Data — ') + network.name, cat: tab, amt: -amount, col: network.color });
      setStep(null);
      app.nav.success({ title: 'Successful', message: `Your ${tab === 'airtime' ? 'airtime' : (planObj && planObj.label + ' data')} purchase to ${phone} was successful.`,
        rows: [['Type', tab === 'airtime' ? 'Airtime top-up' : 'Data bundle'], ['Network', network.name], ['Phone', phone], plan ? ['Plan', planObj.label + ' · ' + planObj.sub] : ['Amount', fmtN(amount)], ['Fee', '₦0'], ['Total', fmtN(amount), true]] });
    };
    return (
      <Screen title="Airtime & Data" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        <Segmented options={[{ v: 'airtime', label: 'Airtime' }, { v: 'data', label: 'Data Bundle' }]} value={tab} onChange={(v) => { setTab(v); setPlan(null); setAmt(''); }} />
        <Label>Select network</Label>
        <ProviderGrid items={D.NETWORKS} value={net} onPick={(v) => { setNet(v); setPlan(null); }} />
        <Field label="Phone number" placeholder="0801 234 5678" type="number" value={phone} onChange={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))}
          prefix={<I name="user" size={18} color="var(--ink-3)" />}
          suffix={<Tap onClick={() => setPhone('08145872210')}><span style={{ fontSize: 12, fontWeight: 700, color: 'var(--brand)' }}>Me</span></Tap>} />
        <div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: -8, marginBottom: 14 }}>Your number · tap to edit, or use “Me” to reset</div>
        {tab === 'airtime' ? (<>
          <Label>Choose amount</Label>
          <QuickAmounts amounts={D.QUICK_AMTS} value={amt} onPick={(a) => setAmt(String(a))} />
          <Field label="Or enter amount" placeholder="0.00" type="number" value={amt} onChange={(v) => setAmt(v.replace(/\D/g, ''))} prefix={<span style={{ fontWeight: 800, color: 'var(--ink-2)' }}>₦</span>} />
          <BalanceHint amount={amount} />
        </>) : (<>
          <Label>Select a data plan</Label>
          <PlanList plans={D.DATA_PLANS[net]} value={plan} onPick={setPlan} />
          <div style={{ height: 12 }} />
          <BalanceHint amount={amount} />
        </>)}
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: tab === 'airtime' ? 'Confirm airtime' : 'Confirm data', icon: tab === 'airtime' ? 'airtime' : 'data', iconColor: network.color,
            rows: [['Network', network.name], ['Phone', phone], plan ? ['Plan', planObj.label] : ['Amount', fmtN(amount)], ['Fee', '₦0']] }} />
      </Screen>
    );
  }

  // ============ CABLE TV ============
  function CableTV() {
    const app = useApp();
    const [prov, setProv] = useState('dstv');
    const [card, setCard] = useState('');
    const [plan, setPlan] = useState(null);
    const { step, setStep } = useCheckout();
    const provider = D.CABLE.find(c => c.id === prov);
    const planObj = (D.CABLE_PLANS[prov] || []).find(p => p.id === plan);
    const amount = planObj ? planObj.price : 0;
    const valid = card.length >= 8 && amount > 0;
    const finish = () => {
      app.pay(amount, { mono: provider.name.slice(0, 2).toUpperCase(), t: provider.name + ' ' + planObj.label, cat: 'tv', amt: -amount, col: provider.color });
      setStep(null);
      app.nav.success({ title: 'Subscription active', message: `${provider.name} ${planObj.label} on ${card} is now active.`,
        rows: [['Provider', provider.name], ['Smartcard / IUC', card], ['Plan', planObj.label], ['Fee', '₦0'], ['Total', fmtN(amount), true]] });
    };
    return (
      <Screen title="Cable TV" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        <Label>Select provider</Label>
        <ProviderGrid items={D.CABLE} value={prov} onPick={(v) => { setProv(v); setPlan(null); }} />
        <Field label="Smartcard / IUC number" placeholder="1234 5678 90" type="number" value={card} onChange={(v) => setCard(v.replace(/\D/g, '').slice(0, 12))} prefix={<I name="tv" size={18} color="var(--ink-3)" />} />
        {card.length >= 8 && <div style={{ marginTop: -4, marginBottom: 14, padding: '10px 14px', borderRadius: 12, background: 'rgba(15,162,149,.1)', fontSize: 12.5, color: 'var(--brand-deep)', fontWeight: 600 }}>✓ ADEYEMI WILLIAM · {provider.name}</div>}
        <Label>Choose a bouquet</Label>
        <PlanList plans={D.CABLE_PLANS[prov]} value={plan} onPick={setPlan} />
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: 'Confirm subscription', icon: 'tv', iconColor: provider.color, rows: [['Provider', provider.name], ['Smartcard', card], plan ? ['Plan', planObj.label] : ['', ''], ['Fee', '₦0']] }} />
      </Screen>
    );
  }

  // ============ ELECTRICITY ============
  function Electricity() {
    const app = useApp();
    const [disco, setDisco] = useState('ikeja');
    const [type, setType] = useState('prepaid');
    const [meter, setMeter] = useState('');
    const [amt, setAmt] = useState('');
    const { step, setStep } = useCheckout();
    const provider = D.DISCOS.find(c => c.id === disco);
    const amount = Number(amt || 0);
    const valid = meter.length >= 8 && amount >= 500 && amount <= app.balance;
    const finish = () => {
      app.pay(amount, { mono: provider.name.slice(0, 2).toUpperCase(), t: provider.name, cat: 'electricity', amt: -amount, col: provider.color });
      setStep(null);
      app.nav.success({ title: 'Token generated', message: `Your ${provider.name} ${type} purchase was successful.`,
        rows: [['Disco', provider.name], ['Meter', meter], ['Type', type], ['Token', '1234 5678 9012 3456 7890'], ['Units', (amount / 65).toFixed(1) + ' kWh'], ['Total', fmtN(amount), true]] });
    };
    return (
      <Screen title="Electricity" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        <Label>Select disco</Label>
        <ProviderGrid items={D.DISCOS} value={disco} onPick={setDisco} cols={3} />
        <Segmented options={[{ v: 'prepaid', label: 'Prepaid' }, { v: 'postpaid', label: 'Postpaid' }]} value={type} onChange={setType} />
        <Field label="Meter number" placeholder="01234567890" type="number" value={meter} onChange={(v) => setMeter(v.replace(/\D/g, '').slice(0, 13))} prefix={<I name="bills" size={18} color="var(--ink-3)" />} />
        <Label>Amount</Label>
        <QuickAmounts amounts={[1000, 2000, 5000, 10000, 20000, 50000]} value={amt} onPick={(a) => setAmt(String(a))} />
        <Field placeholder="Enter amount (min ₦500)" type="number" value={amt} onChange={(v) => setAmt(v.replace(/\D/g, ''))} prefix={<span style={{ fontWeight: 800, color: 'var(--ink-2)' }}>₦</span>} />
        <BalanceHint amount={amount} />
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: 'Confirm payment', icon: 'bills', iconColor: provider.color, rows: [['Disco', provider.name], ['Meter', meter], ['Type', type], ['Fee', '₦0']] }} />
      </Screen>
    );
  }

  // ============ BETTING ============
  function Betting() {
    const app = useApp();
    const [prov, setProv] = useState('bet9ja');
    const [uid, setUid] = useState('');
    const [amt, setAmt] = useState('');
    const { step, setStep } = useCheckout();
    const provider = D.BETTING.find(c => c.id === prov);
    const amount = Number(amt || 0);
    const valid = uid.length >= 4 && amount >= 100 && amount <= app.balance;
    const finish = () => {
      app.pay(amount, { mono: provider.name.slice(0, 2).toUpperCase(), t: provider.name + ' funding', cat: 'betting', amt: -amount, col: provider.color });
      setStep(null);
      app.nav.success({ title: 'Wallet funded', message: `${fmtN(amount)} added to your ${provider.name} account ${uid}.`,
        rows: [['Platform', provider.name], ['User ID', uid], ['Fee', '₦0'], ['Total', fmtN(amount), true]] });
    };
    return (
      <Screen title="Betting" sub="Fund your betting wallet instantly" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        <Label>Select platform</Label>
        <ProviderGrid items={D.BETTING} value={prov} onPick={setProv} cols={3} />
        <Field label="User ID" placeholder="Enter betting ID" value={uid} onChange={(v) => setUid(v.replace(/\s/g, ''))} prefix={<I name="dice" size={18} color="var(--ink-3)" />} />
        <Label>Amount</Label>
        <QuickAmounts amounts={[200, 500, 1000, 2000, 5000, 10000]} value={amt} onPick={(a) => setAmt(String(a))} />
        <Field placeholder="Enter amount" type="number" value={amt} onChange={(v) => setAmt(v.replace(/\D/g, ''))} prefix={<span style={{ fontWeight: 800, color: 'var(--ink-2)' }}>₦</span>} />
        <BalanceHint amount={amount} />
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: 'Confirm funding', icon: 'dice', iconColor: provider.color, rows: [['Platform', provider.name], ['User ID', uid], ['Fee', '₦0']] }} />
      </Screen>
    );
  }

  // ============ EXAMS (JAMB / WAEC) ============
  function Exams() {
    const app = useApp();
    const [exam, setExam] = useState('waec');
    const [qty, setQty] = useState(1);
    const [phone, setPhone] = useState('');
    const { step, setStep } = useCheckout();
    const examObj = D.EXAMS.find(e => e.id === exam);
    const amount = examObj.price * qty;
    const valid = phone.length >= 10;
    const finish = () => {
      app.pay(amount, { mono: examObj.name.slice(0, 2).toUpperCase(), t: examObj.name + ' PIN', cat: 'exams', amt: -amount, col: examObj.color });
      setStep(null);
      app.nav.success({ title: 'PIN purchased', message: `Your ${examObj.name} ${examObj.sub} (${qty}) has been sent to ${phone}.`,
        rows: [['Exam', examObj.name], ['Item', examObj.sub], ['Quantity', String(qty)], ['Phone', phone], ['Total', fmtN(amount), true]] });
    };
    return (
      <Screen title="Exams · JAMB / WAEC" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        <Label>Select exam</Label>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 16 }}>
          {D.EXAMS.map(e => {
            const on = exam === e.id;
            return (
              <Tap key={e.id} onClick={() => setExam(e.id)}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 14px', borderRadius: 15, background: 'var(--surface)', border: '2px solid ' + (on ? 'var(--brand)' : 'var(--line)') }}>
                  <Monogram text={e.name.slice(0, 2)} color={e.color} />
                  <div style={{ flex: 1 }}><div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>{e.name}</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>{e.sub}</div></div>
                  <div className="z-num" style={{ fontWeight: 700, color: on ? 'var(--brand)' : 'var(--ink-1)' }}>{fmtK(e.price)}</div>
                </div>
              </Tap>
            );
          })}
        </div>
        <Label>Quantity</Label>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
          <Tap onClick={() => setQty(q => Math.max(1, q - 1))}><div style={{ width: 46, height: 46, borderRadius: 13, border: '1.5px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)' }}><span style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink-1)' }}>−</span></div></Tap>
          <div className="z-num" style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink-1)', minWidth: 28, textAlign: 'center' }}>{qty}</div>
          <Tap onClick={() => setQty(q => Math.min(10, q + 1))}><div style={{ width: 46, height: 46, borderRadius: 13, border: '1.5px solid var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--surface)' }}><span style={{ fontSize: 22, fontWeight: 700, color: 'var(--ink-1)' }}>+</span></div></Tap>
        </div>
        <Field label="Phone number (PIN delivery)" placeholder="0801 234 5678" type="number" value={phone} onChange={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))} prefix={<I name="user" size={18} color="var(--ink-3)" />} />
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: 'Confirm purchase', icon: 'jamb', iconColor: examObj.color, rows: [['Exam', examObj.name], ['Item', examObj.sub], ['Quantity', String(qty)], ['Phone', phone]] }} />
      </Screen>
    );
  }

  // ============ TRANSFER ============
  function Transfer({ initialAcct }) {
    const app = useApp();
    const [mode, setMode] = useState('bank');
    const [bank, setBank] = useState(null);
    const [acct, setAcct] = useState(initialAcct ? initialAcct.replace(/\D/g, '').slice(0, 10) : '');
    const [amt, setAmt] = useState('');
    const [note, setNote] = useState('');
    const [picked, setPicked] = useState(null);
    const [bankSheet, setBankSheet] = useState(false);
    const [q, setQ] = useState('');
    const { step, setStep } = useCheckout();
    const bankObj = D.BANKS.find(b => b.id === bank);
    const amount = Number(amt || 0);
    // auto-detect bank once a 10-digit account number is entered
    React.useEffect(() => {
      if (mode === 'bank' && acct.length === 10 && !bank) setBank(D.BANKS[Number(acct[0] || 0) % D.BANKS.length].id);
    }, [acct, mode]);
    const acctReady = mode === 'bank' ? (acct.length === 10 && bank) : acct.length >= 4;
    const resolvedName = picked ? picked.name : (acctReady ? 'ADEYEMI WILLIAM' : '');
    const valid = (picked || acctReady) && amount > 0 && amount <= app.balance;
    const finish = () => {
      const bankName = mode === 'zitch' ? 'Zitch' : (picked ? picked.bank : (bankObj ? bankObj.name : 'Bank'));
      const col = mode === 'zitch' ? '#0FA295' : (bankObj ? bankObj.color : '#0FA295');
      app.pay(amount, { mono: (resolvedName || 'ZT').split(' ').map(w => w[0]).join('').slice(0, 2), t: 'Transfer — ' + resolvedName, cat: 'transfer', amt: -amount, col });
      setStep(null);
      app.nav.success({ title: 'Money sent', message: `${fmtN(amount)} sent to ${resolvedName}.`,
        rows: [['Recipient', resolvedName], ['Account', picked ? picked.acct : acct], ['Bank', bankName], note ? ['Note', note] : ['Fee', '₦0'], ['Total', fmtN(amount), true]] });
      if (!picked) {
        const init = (resolvedName || 'ZT').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();
        app.addBeneficiary({ id: 'b' + Date.now(), name: resolvedName, acct: acct, bank: bankName, init, color: col });
        setTimeout(() => app.toast('Saved to beneficiaries'), 500);
      }
    };
    const bankInitials = (b) => (b.name || '').replace(/[^A-Za-z ]/g, '').split(' ').map(w => w[0] || '').join('').slice(0, 2).toUpperCase();
    return (
      <Screen title="Send money" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        {!picked && (<>
          <Label>Saved beneficiaries</Label>
          <Field placeholder="Search by name or account" value={q} onChange={setQ} prefix={<I name="search" size={18} color="var(--ink-3)" />} />
          {(() => {
            const list = app.beneficiaries.filter((b) => (b.name + ' ' + b.acct).toLowerCase().includes(q.toLowerCase()));
            if (!list.length) return <div style={{ fontSize: 13, color: 'var(--ink-3)', padding: '2px 2px 14px' }}>No matching beneficiary</div>;
            return <div style={{ display: 'flex', gap: 14, overflowX: 'auto', paddingBottom: 8, marginBottom: 8 }}>
              {list.map((b) => (
                <Tap key={b.id} onClick={() => setPicked(b)}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7, width: 64 }}>
                    <Monogram text={b.init} color={b.color} size={52} r={26} />
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--ink-2)', textAlign: 'center', lineHeight: 1.1 }}>{b.name.split(' ')[0]}</span>
                  </div>
                </Tap>
              ))}
            </div>;
          })()}
        </>)}
        {picked ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px', borderRadius: 16, background: 'var(--surface)', border: '1.5px solid var(--line)', marginBottom: 16 }}>
            <Monogram text={picked.init} color={picked.color} />
            <div style={{ flex: 1 }}><div style={{ fontWeight: 700, color: 'var(--ink-1)' }}>{picked.name}</div><div className="z-num" style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>{picked.acct} · {picked.bank}</div></div>
            <Tap onClick={() => setPicked(null)}><span style={{ fontSize: 13, fontWeight: 700, color: 'var(--brand)' }}>Change</span></Tap>
          </div>
        ) : mode === 'bank' ? (<>
          {/* account number FIRST */}
          <Field label="Account number" placeholder="Enter 10-digit account number" type="number" value={acct} onChange={(v) => setAcct(v.replace(/\D/g, '').slice(0, 10))} prefix={<I name="bank" size={18} color="var(--ink-3)" />} />
          {/* bank: auto-detected, or tap to pick */}
          <Field label="Bank" readOnly value={bankObj ? bankObj.name : ''} placeholder={acct.length === 10 ? 'Select bank' : 'Auto-detected after account number'} onClick={() => setBankSheet(true)}
            prefix={bankObj ? <span style={{ width: 18, height: 18, borderRadius: 5, background: bankObj.color, display: 'inline-block' }} /> : <I name="bank" size={18} color="var(--ink-3)" />}
            suffix={<I name="down" size={16} color="var(--ink-3)" />} />
          {resolvedName && <div style={{ marginTop: -4, marginBottom: 14, padding: '10px 14px', borderRadius: 12, background: 'rgba(15,162,149,.1)', fontSize: 12.5, color: 'var(--brand-deep)', fontWeight: 700 }}>✓ {resolvedName}</div>}
        </>) : (<>
          <Field label="Zitch tag or phone" placeholder="@username / 0801…" value={acct} onChange={(v) => setAcct(v.slice(0, 11))} prefix={<I name="user" size={18} color="var(--ink-3)" />} />
          {resolvedName && <div style={{ marginTop: -4, marginBottom: 14, padding: '10px 14px', borderRadius: 12, background: 'rgba(15,162,149,.1)', fontSize: 12.5, color: 'var(--brand-deep)', fontWeight: 700 }}>✓ {resolvedName}</div>}
        </>)}
        <Label>Amount</Label>
        <QuickAmounts amounts={[1000, 2000, 5000, 10000, 20000, 50000]} value={amt} onPick={(a) => setAmt(String(a))} />
        <Field placeholder="Enter amount" type="number" value={amt} onChange={(v) => setAmt(v.replace(/\D/g, ''))} prefix={<span style={{ fontWeight: 800, color: 'var(--ink-2)' }}>₦</span>} />
        <BalanceHint amount={amount} />
        <Field label="Narration (optional)" placeholder="What's it for?" value={note} onChange={setNote} />
        {bankSheet && <OptionSheet title="Select bank" items={D.BANKS} onClose={() => setBankSheet(false)} onPick={(b) => setBank(b.id)}
          renderItem={(b, i) => (
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '12px 0', borderTop: i > 0 ? '1px solid var(--line)' : 'none' }}>
              <Monogram text={bankInitials(b)} color={b.color} />
              <div style={{ flex: 1, fontWeight: 600, color: 'var(--ink-1)' }}>{b.name}</div>
              {bank === b.id && <I name="check" size={18} color="var(--brand)" />}
            </div>
          )} />}
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: 'Confirm transfer', icon: 'send', iconColor: 'var(--brand)', rows: [['To', resolvedName], ['Account', picked ? picked.acct : acct], ['Bank', mode === 'zitch' ? 'Zitch' : (picked ? picked.bank : (bankObj ? bankObj.name : '—'))], ['Fee', '₦0']] }} />
      </Screen>
    );
  }

  // ============ GET LOAN ============
  function Loan() {
    const app = useApp();
    const max = 500000;
    const [amt, setAmt] = useState(150000);
    const [tenure, setTenure] = useState(30);
    const [step, setStep] = useState(null);
    const rate = 0.045;
    const interest = Math.round(amt * rate * (tenure / 30));
    const repay = amt + interest;
    const finish = () => {
      app.fund(amt);
      app.addTxn({ mono: 'LN', t: 'Loan disbursed', cat: 'loan', amt: amt, col: '#0BA12B' });
      setStep(null);
      app.nav.success({ title: 'Loan disbursed', message: `${fmtN(amt)} has been added to your wallet. Repay by due date to boost your limit.`,
        rows: [['Loan amount', fmtN(amt)], ['Interest (' + (rate * 100) + '%)', fmtN(interest)], ['Tenure', tenure + ' days'], ['Repayment', fmtN(repay), true]] });
    };
    return (
      <Screen title="Get Loan" sub="Instant, no paperwork" onBack={app.nav.pop}
        footer={<PrimaryButton label={'Get ' + fmtN(amt)} onClick={() => setStep('confirm')} />}>
        <div style={{ borderRadius: 20, padding: 20, background: 'var(--hero-grad)', color: '#fff', marginBottom: 18, position: 'relative', overflow: 'hidden' }}>
          <div style={{ fontSize: 13, opacity: .85 }}>You're eligible for up to</div>
          <div className="z-num" style={{ fontSize: 34, fontWeight: 800, marginTop: 4 }}>{fmtN(max)}</div>
          <div style={{ fontSize: 12.5, opacity: .85, marginTop: 6 }}>Based on your Zitch activity & repayment history</div>
        </div>
        <Label>How much do you need?</Label>
        <div className="z-num" style={{ fontSize: 32, fontWeight: 800, color: 'var(--brand)', textAlign: 'center', marginBottom: 6 }}>{fmtN(amt)}</div>
        <input type="range" min={10000} max={max} step={5000} value={amt} onChange={(e) => setAmt(Number(e.target.value))}
          style={{ width: '100%', accentColor: 'var(--brand)', marginBottom: 6 }} />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--ink-3)', marginBottom: 18 }}><span className="z-num">₦10,000</span><span className="z-num">{fmtN(max)}</span></div>
        <Label>Repayment period</Label>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginBottom: 18 }}>
          {[15, 30, 60].map(t => (
            <Tap key={t} onClick={() => setTenure(t)}>
              <div style={{ textAlign: 'center', padding: '14px', borderRadius: 14, fontWeight: 700, background: tenure === t ? 'var(--brand)' : 'var(--surface)', color: tenure === t ? '#fff' : 'var(--ink-1)', border: '1.5px solid ' + (tenure === t ? 'var(--brand)' : 'var(--line)') }}>{t} days</div>
            </Tap>
          ))}
        </div>
        <div style={{ borderRadius: 16, background: 'var(--surface)', border: '1.5px solid var(--line)', padding: '4px 16px 12px' }}>
          {[['Interest', fmtN(interest)], ['Tenure', tenure + ' days'], ['Total repayment', fmtN(repay), true]].map((r, i) => <window.Row2 key={i} k={r[0]} v={r[1]} strong={r[2]} />)}
        </div>
        {step === 'confirm' && <ConfirmSheet title="Confirm loan" icon="loan" iconColor="#0BA12B" total={amt} rows={[['Amount', fmtN(amt)], ['Interest', fmtN(interest)], ['Tenure', tenure + ' days'], ['Repay', fmtN(repay)]]} onPay={() => setStep(app.biometrics ? 'bio' : 'pin')} onClose={() => setStep(null)} />}
        {step === 'bio' && <BiometricScan title="Approve loan" subtitle={'Authorize ' + fmtN(amt)} onDone={finish} onFallback={() => setStep('pin')} onClose={() => setStep(null)} />}
        {step === 'pin' && <PinSheet amount={amt} onDone={finish} onClose={() => setStep(null)} onBio={app.biometrics ? () => setStep('bio') : null} />}
      </Screen>
    );
  }

  // ============ ADD MONEY ============
  function MethodRow({ icon, title, sub, onClick }) {
    return (
      <Tap onClick={onClick}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '13px 14px', borderRadius: 16, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', marginBottom: 11 }}>
          <div style={{ width: 44, height: 44, borderRadius: 13, background: 'var(--surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name={icon} size={22} color="var(--brand)" /></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>{title}</div>
            <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 2 }}>{sub}</div>
          </div>
          <I name="right" size={18} color="var(--ink-3)" />
        </div>
      </Tap>
    );
  }

  function AddMoney() {
    const app = useApp();
    const [acct, setAcct] = useState(null); // null until provisioned via BVN
    const [bvn, setBvn] = useState('');
    const [busy, setBusy] = useState(false);
    const grouped = (s) => String(s).replace(/(\d{4})(\d{3})(\d{3})/, '$1 $2 $3');
    const createAcct = () => {
      if (bvn.length !== 11 || busy) return;
      setBusy(true);
      setTimeout(() => { setBusy(false); setAcct({ number: '9012345678', bank: 'Providus Bank', name: 'WILLIAM ADEYEMI' }); app.toast('Your account is ready'); }, 1600);
    };
    return (
      <Screen title="Add money" onBack={app.nav.pop}>
        {!acct ? (
          <div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', padding: '8px 8px 0' }}>
              <div style={{ width: 84, height: 84, borderRadius: 24, background: 'rgba(15,162,149,.14)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="bank" size={40} color="var(--brand)" /></div>
              <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink-1)', marginTop: 16 }}>Get your Zitch account number</div>
              <div style={{ fontSize: 13.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.5, maxWidth: 300 }}>Enter your BVN to instantly get a dedicated account for funding by bank transfer.</div>
            </div>
            <div style={{ marginTop: 24 }}>
              <Field label="Bank Verification Number (BVN)" placeholder="Enter your 11-digit BVN" type="number" value={bvn} onChange={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} prefix={<I name="insurance" size={18} color="var(--ink-3)" />} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 2px 0', color: 'var(--ink-3)', fontSize: 12.5 }}><I name="help" size={14} color="var(--z-amber)" />Dial *565*0# on your registered line to get your BVN.</div>
              <div style={{ height: 22 }} />
              <PrimaryButton label={busy ? 'Creating your account…' : 'Get my account'} disabled={bvn.length !== 11} onClick={createAcct} />
            </div>
          </div>
        ) : (
          <div>
            <div style={{ borderRadius: 18, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', padding: '16px', marginBottom: 16 }}>
              <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--ink-1)' }}>Fund by bank transfer</div>
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 3 }}>Send money to this account from any bank.</div>
              <div style={{ borderTop: '1px dashed var(--line)', margin: '14px 0' }} />
              <div style={{ fontSize: 12.5, color: 'var(--ink-3)' }}>{acct.bank}</div>
              <div className="z-num" style={{ fontSize: 26, fontWeight: 800, color: 'var(--ink-1)', letterSpacing: '1.5px', margin: '6px 0 2px' }}>{grouped(acct.number)}</div>
              <div style={{ fontSize: 13, color: 'var(--ink-2)', fontWeight: 600 }}>{acct.name}</div>
              <div style={{ marginTop: 14 }}>
                <Tap onClick={() => { try { navigator.clipboard.writeText(acct.number); } catch (e) { } app.toast('Account number copied'); }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, padding: '12px', borderRadius: 12, background: 'rgba(15,162,149,.14)', color: 'var(--brand-deep)', fontWeight: 700, fontSize: 14 }}><I name="copy" size={16} color="var(--brand-deep)" />Copy</div>
                </Tap>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 10, padding: '12px 14px', borderRadius: 14, background: 'var(--surface-2)', border: '1px solid var(--line)', marginBottom: 16 }}>
              <I name="check" size={18} color="var(--brand)" style={{ flexShrink: 0, marginTop: 1 }} />
              <div style={{ fontSize: 12.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>Save this account — it's permanently yours. Transfers reflect automatically, no need to confirm anything here.</div>
            </div>
            <MethodRow icon="bank" title="Cash Deposit" sub="Fund your account with nearby agents" onClick={() => app.nav.push('coming', { title: 'Cash Deposit', icon: 'bank', note: 'Find a Zitch agent near you to deposit cash.' })} />
            <MethodRow icon="qr" title="Scan my QR Code" sub="Show QR code to any Zitch user" onClick={() => app.nav.push('coming', { title: 'My QR Code', icon: 'qr', note: 'Let others scan to pay you instantly.' })} />
          </div>
        )}
      </Screen>
    );
  }

  function AccountDetails() {
    const app = useApp();
    const [first, setFirst] = useState('William');
    const [last, setLast] = useState('Adeyemi');
    const [email, setEmail] = useState('william.adeyemi@gmail.com');
    const [phone, setPhone] = useState('08145872210');
    const emailOk = /.+@.+\..+/.test(email);
    const phoneOk = phone.replace(/\D/g, '').length >= 10;
    const valid = first.trim() && last.trim() && emailOk && phoneOk;
    return (
      <Screen title="Account details" onBack={app.nav.pop} footer={<PrimaryButton label="Save changes" disabled={!valid} onClick={() => { app.toast('Profile updated'); app.nav.pop(); }} />}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 18 }}>
          <div style={{ position: 'relative' }}>
            {React.createElement(window.Avatar, { size: 84, ring: 'var(--brand)' })}
            <div style={{ position: 'absolute', right: -2, bottom: -2, width: 30, height: 30, borderRadius: '50%', background: 'var(--brand)', border: '3px solid var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name="plus" size={15} color="#fff" stroke={2.6} /></div>
          </div>
          <Tap onClick={() => app.toast('Choose a new photo')}><span style={{ fontSize: 13, fontWeight: 700, color: 'var(--brand)', marginTop: 10, display: 'inline-block' }}>Change photo</span></Tap>
        </div>
        <Field label="First name" value={first} onChange={setFirst} prefix={<I name="user" size={18} color="var(--ink-3)" />} />
        <Field label="Last name" value={last} onChange={setLast} prefix={<I name="user" size={18} color="var(--ink-3)" />} />
        <Field label="Email" value={email} onChange={setEmail} placeholder="you@email.com" prefix={<I name="remita" size={18} color="var(--ink-3)" />} />
        <Field label="Phone" type="number" value={phone} onChange={(v) => setPhone(v.replace(/\D/g, '').slice(0, 11))} prefix={<I name="airtime" size={18} color="var(--ink-3)" />} />
      </Screen>
    );
  }
  window.AccountDetails = AccountDetails;

  function KYC() {
    const app = useApp();
    const [method, setMethod] = useState(null);
    const [bvn, setBvn] = useState('');
    const [otp, setOtp] = useState('');
    const [otpSent, setOtpSent] = useState(false);
    const [nin, setNin] = useState('');
    const [ninPhoto, setNinPhoto] = useState(false);
    const [busy, setBusy] = useState(false);
    const [scan, setScan] = useState(false);
    const finish = (msg) => { setBusy(false); app.toast(msg); app.nav.pop(); };
    const sendBvnOtp = () => { if (bvn.length !== 11 || busy) return; setBusy(true); setTimeout(() => { setBusy(false); setOtpSent(true); app.toast('Code sent to your BVN phone'); }, 1300); };
    const confirmBvn = () => { if (otp.length !== 6 || busy) return; setBusy(true); setTimeout(() => finish('BVN verified — tier upgraded'), 1400); };
    const verifyNin = () => { if (!(nin.length === 11 && ninPhoto) || busy) return; setBusy(true); setTimeout(() => finish('NIN submitted for review'), 1500); };
    const runSelfie = () => { if (scan) return; setScan(true); setTimeout(() => { setScan(false); finish('Selfie verified — liveness passed'); }, 2300); };
    const back = method ? () => { setMethod(null); setOtpSent(false); setBusy(false); setScan(false); } : app.nav.pop;
    const Footer = () => <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, marginTop: 18, color: 'var(--ink-3)', fontSize: 12 }}><I name="insurance" size={13} color="var(--ink-3)" />BVN/NIN are never stored in full</div>;
    const Hero = ({ icon, color, title, sub }) => <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', padding: '4px 8px 0' }}>
      <div style={{ width: 80, height: 80, borderRadius: 24, background: color + '22', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><I name={icon} size={38} color={color} /></div>
      <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink-1)', marginTop: 14 }}>{title}</div>
      <div style={{ fontSize: 13.5, color: 'var(--ink-3)', marginTop: 6, lineHeight: 1.5, maxWidth: 300 }}>{sub}</div>
    </div>;
    const MethodCard = ({ id, icon, color, title, sub, badge }) => <Tap onClick={() => setMethod(id)}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 13, padding: '14px 15px', borderRadius: 16, background: 'var(--surface)', boxShadow: 'var(--shadow-card)', border: '1px solid var(--line)', marginBottom: 12 }}>
        <div style={{ width: 46, height: 46, borderRadius: 13, background: color + '22', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name={icon} size={22} color={color} /></div>
        <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 700, color: 'var(--ink-1)' }}>{title}</div><div style={{ fontSize: 12.5, color: 'var(--ink-3)', marginTop: 1 }}>{sub}</div></div>
        {badge ? <span style={{ fontSize: 10, fontWeight: 700, padding: '3px 8px', borderRadius: 999, background: 'rgba(15,162,149,.14)', color: 'var(--brand-deep)', whiteSpace: 'nowrap' }}>{badge}</span> : <I name="right" size={18} color="var(--ink-3)" />}
      </div>
    </Tap>;
    return (
      <Screen title="Identity verification" onBack={back}>
        {!method && <div>
          <div style={{ fontSize: 13.5, color: 'var(--ink-3)', lineHeight: 1.5, marginBottom: 18 }}>Verify your identity to raise your limits and unlock every Zitch feature.</div>
          <MethodCard id="bvn" icon="insurance" color="#0FA295" title="BVN verification" sub="Fastest · Bank Verification Number" badge="Recommended" />
          <MethodCard id="nin" icon="card" color="#2D7FF9" title="NIN verification" sub="National ID number + photo of your slip" />
          <MethodCard id="selfie" icon="user" color="#7A5CFF" title="Selfie verification" sub="Quick liveness check with your camera" />
          <Footer />
        </div>}

        {method === 'bvn' && !otpSent && <div>
          <Hero icon="insurance" color="#0FA295" title="BVN verification" sub="Enter your 11-digit BVN. We'll send a code to the phone number linked to it." />
          <div style={{ marginTop: 22 }}>
            <Field label="Bank Verification Number (BVN)" placeholder="Enter your 11-digit BVN" type="number" value={bvn} onChange={(v) => setBvn(v.replace(/\D/g, '').slice(0, 11))} prefix={<I name="insurance" size={18} color="var(--ink-3)" />} />
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 2px 0', color: 'var(--ink-3)', fontSize: 12.5 }}><I name="help" size={14} color="var(--z-amber)" />Dial *565*0# on your registered line to get your BVN.</div>
            <div style={{ height: 22 }} />
            <PrimaryButton label={busy ? 'Sending code…' : 'Send verification code'} disabled={bvn.length !== 11} onClick={sendBvnOtp} />
          </div>
          <Footer />
        </div>}

        {method === 'bvn' && otpSent && <div>
          <Hero icon="insurance" color="#0FA295" title="Confirm your BVN" sub="Enter the 6-digit code we sent to the phone linked to your BVN." />
          <div style={{ marginTop: 22 }}>
            <Field label="Verification code" placeholder="6-digit code" type="number" value={otp} onChange={(v) => setOtp(v.replace(/\D/g, '').slice(0, 6))} prefix={<I name="lock" size={18} color="var(--ink-3)" />} />
            <div style={{ fontSize: 12.5, color: 'var(--ink-3)', margin: '2px 2px 0' }}>Didn't get it? <span style={{ color: 'var(--brand)', fontWeight: 700 }}>Resend in 0:24</span></div>
            <div style={{ height: 22 }} />
            <PrimaryButton label={busy ? 'Confirming…' : 'Confirm BVN'} disabled={otp.length !== 6} onClick={confirmBvn} />
          </div>
          <Footer />
        </div>}

        {method === 'nin' && <div>
          <Hero icon="card" color="#2D7FF9" title="NIN verification" sub="Enter your NIN and upload a clear photo of your NIN slip or ID card." />
          <div style={{ marginTop: 22 }}>
            <Field label="National Identification Number (NIN)" placeholder="Enter your 11-digit NIN" type="number" value={nin} onChange={(v) => setNin(v.replace(/\D/g, '').slice(0, 11))} prefix={<I name="card" size={18} color="var(--ink-3)" />} />
            <Tap onClick={() => { setNinPhoto(true); app.toast('Photo added'); }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px', borderRadius: 14, border: '1.5px dashed ' + (ninPhoto ? 'var(--brand)' : 'var(--line)'), background: ninPhoto ? 'rgba(15,162,149,.08)' : 'var(--surface-2)', marginBottom: 4 }}>
                <div style={{ width: 40, height: 40, borderRadius: 11, background: ninPhoto ? 'rgba(15,162,149,.16)' : 'var(--surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}><I name={ninPhoto ? 'check' : 'plus'} size={20} color={ninPhoto ? 'var(--brand)' : 'var(--ink-3)'} stroke={2.4} /></div>
                <div style={{ flex: 1 }}><div style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink-1)' }}>{ninPhoto ? 'NIN_slip.jpg' : 'Upload photo of your NIN slip / ID'}</div><div style={{ fontSize: 12, color: 'var(--ink-3)', marginTop: 1 }}>{ninPhoto ? 'Tap to replace' : 'JPG or PNG · max 5MB'}</div></div>
              </div>
            </Tap>
            <div style={{ height: 18 }} />
            <PrimaryButton label={busy ? 'Verifying…' : 'Verify NIN'} disabled={!(nin.length === 11 && ninPhoto)} onClick={verifyNin} />
          </div>
          <Footer />
        </div>}

        {method === 'selfie' && <div>
          <Hero icon="user" color="#7A5CFF" title="Selfie verification" sub="Hold your phone at eye level and keep your face inside the circle." />
          <div style={{ display: 'flex', justifyContent: 'center', margin: '22px 0' }}>
            <div style={{ position: 'relative', width: 180, height: 180, borderRadius: '50%', background: 'var(--surface-2)', border: '2px dashed var(--z-teal-200, #8FDDD4)', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
              <I name="user" size={92} color="var(--ink-3)" />
              {scan && <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(5,32,28,.45)' }}><svg width="180" height="180" viewBox="0 0 180 180" style={{ position: 'absolute', animation: 'zspin 1s linear infinite' }}><circle cx="90" cy="90" r="84" fill="none" stroke="rgba(255,255,255,.25)" strokeWidth="4" /><path d="M90 6 a84 84 0 0 1 73 42" fill="none" stroke="#5CF5EB" strokeWidth="4" strokeLinecap="round" /></svg></div>}
            </div>
          </div>
          <div style={{ textAlign: 'center', fontSize: 13, color: scan ? 'var(--brand)' : 'var(--ink-3)', fontWeight: scan ? 700 : 500, marginBottom: 14 }}>{scan ? 'Checking liveness…' : 'Front camera · no Face ID needed'}</div>
          <PrimaryButton label={scan ? 'Verifying…' : 'Start camera'} disabled={scan} onClick={runSelfie} />
          <Footer />
        </div>}
      </Screen>
    );
  }
  window.KYC = KYC;

  // ============ FIXED SAVE ============
  function FixedSave() {
    const app = useApp();
    const RATES = { 30: 0.12, 90: 0.15, 180: 0.18, 365: 0.22 };
    const [amt, setAmt] = useState('');
    const [days, setDays] = useState(90);
    const { step, setStep } = useCheckout();
    const amount = Number(amt || 0);
    const rate = RATES[days];
    const interest = Math.round(amount * rate * (days / 365));
    const maturity = amount + interest;
    const valid = amount >= 1000 && amount <= app.balance;
    const finish = () => {
      app.pay(amount, { mono: 'FS', t: 'Fixed Save locked', cat: 'save', amt: -amount, col: '#0FA295' });
      setStep(null);
      app.nav.success({ title: 'Savings locked 🔒', message: `${fmtN(amount)} locked for ${days} days at ${(rate * 100)}% p.a. You can't withdraw until maturity.`,
        rows: [['Principal', fmtN(amount)], ['Rate', (rate * 100) + '% p.a'], ['Duration', days + ' days'], ['Interest earned', fmtN(interest)], ['Maturity value', fmtN(maturity), true]] });
    };
    return (
      <Screen title="Fixed Save" sub="Lock funds, earn up to 22% p.a" onBack={app.nav.pop}
        footer={<PrimaryButton label="Continue" disabled={!valid} onClick={() => setStep('confirm')} />}>
        <div style={{ borderRadius: 20, padding: 20, background: 'var(--hero-grad)', color: '#fff', marginBottom: 18, position: 'relative', overflow: 'hidden' }}>
          <div style={{ fontSize: 13, opacity: .85 }}>You could earn</div>
          <div className="z-num" style={{ fontSize: 32, fontWeight: 800, marginTop: 4 }}>{fmtN(interest)}</div>
          <div style={{ fontSize: 12.5, opacity: .85, marginTop: 6 }}>on {amount > 0 ? fmtN(amount) : '₦0'} in {days} days · {(rate * 100)}% p.a</div>
        </div>
        <Label>How much to lock?</Label>
        <QuickAmounts amounts={[5000, 10000, 20000, 50000, 100000, 200000]} value={amt} onPick={(a) => setAmt(String(a))} />
        <Field placeholder="Enter amount (min ₦1,000)" type="number" value={amt} onChange={(v) => setAmt(v.replace(/\D/g, ''))} prefix={<span style={{ fontWeight: 800, color: 'var(--ink-2)' }}>₦</span>} />
        <BalanceHint amount={amount} />
        <Label>Lock period</Label>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 10, marginBottom: 18 }}>
          {[30, 90, 180, 365].map(d => (
            <Tap key={d} onClick={() => setDays(d)}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px', borderRadius: 14, background: days === d ? 'var(--brand)' : 'var(--surface)', border: '1.5px solid ' + (days === d ? 'var(--brand)' : 'var(--line)') }}>
                <span style={{ fontWeight: 700, color: days === d ? '#fff' : 'var(--ink-1)' }}>{d} days</span>
                <span style={{ fontSize: 12.5, fontWeight: 700, color: days === d ? 'rgba(255,255,255,.85)' : 'var(--brand)' }}>{(RATES[d] * 100)}%</span>
              </div>
            </Tap>
          ))}
        </div>
        <div style={{ borderRadius: 16, background: 'var(--surface)', border: '1.5px solid var(--line)', padding: '4px 16px 12px' }}>
          {[['Interest', fmtN(interest)], ['Matures in', days + ' days'], ['You get back', fmtN(maturity), true]].map((r, i) => <window.Row2 key={i} k={r[0]} v={r[1]} strong={r[2]} />)}
        </div>
        <Sheets step={step} setStep={setStep} amount={amount} finish={finish}
          confirm={{ title: 'Confirm Fixed Save', rows: [['Principal', fmtN(amount)], ['Duration', days + ' days'], ['Rate', (rate * 100) + '% p.a'], ['Maturity value', fmtN(maturity)]] }} />
      </Screen>
    );
  }

  Object.assign(window, { AirtimeData, CableTV, Electricity, Betting, Exams, Transfer, Loan, AddMoney, FixedSave });
})();

// App.jsx — router, device frame, global state, theme + scaling
(function () {
  const { useState, useEffect, useMemo, useRef } = React;
  const EMBED = /[?&]embed=1/.test(window.location.search);
  const AppCtx = window.AppCtx;
  const SuccessReceipt = window.SuccessReceipt;

  const REG = {
    splash: window.Splash, onboarding: window.Onboarding, signin: window.SignIn, register: window.Register, otp: window.Otp, setpin: window.SetPin, biometric: window.Biometric,
    airtime: window.AirtimeData, betting: window.Betting, cable: window.CableTV, electricity: window.Electricity, exams: window.Exams, transfer: window.Transfer, loan: window.Loan, addmoney: window.AddMoney, fixedsave: window.FixedSave,
    history: window.History, txn: window.TxnDetail, notifications: window.Notifications, coming: window.ComingSoon, lock: window.Lock,
    linkbank: window.LinkBank, linkwhatsapp: window.LinkWhatsApp, accountdetails: window.AccountDetails, kyc: window.KYC,
  };
  const TABS = { home: window.Home, wallet: window.Wallet, loans: window.Loans, cards: window.Cards, me: window.Me };
  const DIMS = { phone: { w: 414, h: 868, r: 56 }, fold: { w: 730, h: 880, r: 46 }, tablet: { w: 880, h: 1180, r: 36 } };
  const NAVITEMS = [['home', 'Home'], ['wallet', 'Wallet'], ['loan', 'Loans'], ['card', 'Cards'], ['user', 'Me']];
  const NAVMAP = { home: 'home', wallet: 'wallet', loan: 'loans', card: 'cards', user: 'me' };

  function App() {
    const [theme, setTheme] = useState(() => localStorage.getItem('z-theme') || 'light');
    const [balance, setBalance] = useState(360000);
    const [txns, setTxns] = useState(window.ZDATA.TXNS);
    const [showBal, setShowBal] = useState(true);
    const [biometrics, setBiometrics] = useState(true);
    const [beneficiaries, setBeneficiaries] = useState(window.ZDATA.BENEFICIARIES);
    const addBeneficiary = (b) => setBeneficiaries((p) => (p.some((x) => x.acct === b.acct) ? p : [b, ...p]));
    // linked external bank accounts (Mono) — mirrors /api/banklink/list/
    const [linkedAccounts, setLinkedAccounts] = useState(window.ZDATA.LINKED);
    const [linkedLoading, setLinkedLoading] = useState(false);
    const linkBank = (a) => setLinkedAccounts((p) => [a, ...p]);
    const unlinkBank = (id) => setLinkedAccounts((p) => p.filter((x) => x.id !== id));
    const refreshBank = (id, patch) => setLinkedAccounts((p) => p.map((x) => (x.id === id ? { ...x, ...patch } : x)));
    const refreshLinked = () => { setLinkedLoading(true); setTimeout(() => setLinkedLoading(false), 1100); };
    const [mode, setMode] = useState(EMBED ? 'app' : 'auth');
    const [tab, setTab] = useState('home');
    const [stack, setStack] = useState(EMBED ? [] : [{ key: 'splash' }]);
    const [scale, setScale] = useState(1);
    const [device, setDevice] = useState('phone');
    const [toast, setToast] = useState(null);
    const [detected, setDetected] = useState(null);
    const pasteRef = useRef(false);
    const showToast = (msg, type) => { const id = Date.now(); setToast({ msg, type: type || 'success', id }); setTimeout(() => setToast((t) => (t && t.id === id ? null : t)), 2300); };

    useEffect(() => { localStorage.setItem('z-theme', theme); }, [theme]);
    useEffect(() => { if (mode === 'app') { try { localStorage.setItem('z-onboarded', '1'); } catch (e) { } } }, [mode]);
    // Smart paste: on first app entry, detect a copied phone/account number
    useEffect(() => {
      if (mode !== 'app' || pasteRef.current || EMBED) return;
      pasteRef.current = true;
      const seed = (n) => setTimeout(() => setDetected(n), 950);
      try {
        navigator.clipboard.readText().then((t) => {
          const m = (t || '').replace(/[\s-]/g, '').match(/^\+?(\d{10,11})$/);
          seed(m ? m[1] : '08166938327');
        }).catch(() => seed('08166938327'));
      } catch (e) { seed('08166938327'); }
    }, [mode]);
    const dim = DIMS[device];
    const wide = device !== 'phone';
    const railW = device === 'tablet' ? 240 : 200;
    useEffect(() => {
      const f = () => { if (EMBED) { setScale(Math.min(window.innerHeight / dim.h, window.innerWidth / dim.w)); return; } const sh = (window.innerHeight - 88) / dim.h; const sw = (window.innerWidth - 28) / dim.w; setScale(Math.min(device === 'phone' ? 1.35 : 1, sh, sw)); };
      f(); window.addEventListener('resize', f); return () => window.removeEventListener('resize', f);
    }, [device]);

    const addTxn = (t) => setTxns((p) => [{ id: 'x' + Date.now(), time: 'Just now', status: t.status || 'Successful', ...t }, ...p]);
    const pay = (amount, t) => { setBalance((b) => b - amount); addTxn(t); };
    const fund = (amount) => setBalance((b) => b + amount);

    const nav = useMemo(() => ({
      push: (key, props) => setStack((s) => [...s, { key, props }]),
      pop: () => setStack((s) => s.slice(0, -1)),
      replace: (key, props) => setStack((s) => (s.length ? [...s.slice(0, -1), { key, props }] : [{ key, props }])),
      tab: (name) => { setMode('app'); setTab(name); setStack([]); },
      home: () => { setMode('app'); setTab('home'); setStack([]); },
      success: (r) => setStack((s) => [...s, { key: 'success', props: r }]),
      reset: (key) => { if (key === 'splash') { try { localStorage.removeItem('z-onboarded'); } catch (e) { } setMode('auth'); setStack([{ key: 'splash' }]); } },
      reopen: () => { setMode('auth'); setStack([{ key: 'splash' }]); },
    }), []);

    const app = { theme, setTheme, balance, txns, showBal, setShowBal, biometrics, setBiometrics, beneficiaries, addBeneficiary, pay, fund, addTxn, toast: showToast, detected, clearDetected: () => setDetected(null), device, wide, tab, mode, enterApp: () => { try { localStorage.setItem('z-onboarded', '1'); } catch (e) { } setMode('app'); setTab('home'); setStack([]); }, linkedAccounts, linkedLoading, linkBank, unlinkBank, refreshBank, refreshLinked, nav };

    const renderItem = (item) => {
      if (!item) return null;
      if (item.key === 'success') return React.createElement(SuccessReceipt, { ...item.props, onDone: () => nav.home() });
      const C = REG[item.key]; return C ? React.createElement(C, item.props || {}) : null;
    };

    const base = mode === 'app' ? React.createElement(TABS[tab] || TABS.home) : null;
    const top = stack.length ? stack[stack.length - 1] : null;
    const showNav = mode === 'app' && stack.length === 0;

    return (
      React.createElement(AppCtx.Provider, { value: app },
        React.createElement('div', { style: { position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', background: EMBED ? 'transparent' : 'radial-gradient(120% 90% at 50% 0%, #16302B, #0A1614 70%)', overflow: 'hidden' } },
          // top control bar
          !EMBED && React.createElement('div', { style: { position: 'absolute', top: 0, left: 0, right: 0, height: 54, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px', color: 'rgba(255,255,255,.8)', zIndex: 100 } },
            React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 9, fontSize: 13, fontWeight: 700, letterSpacing: '.04em' } }, React.createElement(window.ZMark, { size: 18 }), 'ZITCH · Prototype'),
            React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
              React.createElement('div', { style: { display: 'flex', gap: 3, padding: 3, background: 'rgba(255,255,255,.08)', borderRadius: 999, marginRight: 4 } },
                ['phone', 'fold', 'tablet'].map((d) => segBtn(d[0].toUpperCase() + d.slice(1), device === d, () => setDevice(d)))),
              ctrlBtn(theme === 'dark' ? '\u2600' : '\u263e', () => setTheme(theme === 'dark' ? 'light' : 'dark')),
              ctrlBtn('Home', () => nav.home()),
              ctrlBtn('Reopen', () => nav.reopen()),
              ctrlBtn('Restart', () => nav.reset('splash')))),
          // device
          React.createElement('div', { style: { zoom: scale } },
            React.createElement('div', { style: { width: dim.w, height: dim.h, borderRadius: EMBED ? 0 : dim.r, background: EMBED ? 'transparent' : '#05100E', padding: EMBED ? 0 : 12, boxShadow: EMBED ? 'none' : '0 40px 90px -30px rgba(0,0,0,.8), inset 0 0 0 2px #1d2a27' } },
              React.createElement('div', { className: 'z-' + theme, style: { position: 'relative', width: '100%', height: '100%', borderRadius: EMBED ? 0 : dim.r - 12, overflow: 'hidden', background: 'var(--bg)', display: 'flex' } },
                wide && mode === 'app' && React.createElement(Sidebar, { app, railW }),
                React.createElement('div', { style: { position: 'absolute', top: 0, bottom: 0, left: (wide && mode === 'app') ? railW : 0, right: 0 } },
                  base,
                  top && React.createElement('div', { key: stack.length + ':' + top.key, style: { position: 'absolute', inset: 0, zIndex: 20 } }, renderItem(top)),
                  showNav && !wide && React.createElement(window.BottomNav, null)),
                toast && React.createElement('div', { key: toast.id, style: { position: 'absolute', top: 60, left: (wide && mode === 'app') ? railW : 0, right: 0, display: 'flex', justifyContent: 'center', zIndex: 200, pointerEvents: 'none' } },
                  React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 9, maxWidth: '86%', padding: '11px 16px', borderRadius: 14, background: 'var(--ink-1)', color: 'var(--bg)', fontSize: 13.5, fontWeight: 600, boxShadow: '0 12px 30px -10px rgba(0,0,0,.5)' } },
                    React.createElement(window.ZIcon, { name: toast.type === 'error' ? 'x' : 'check', size: 16, color: toast.type === 'error' ? 'var(--z-red)' : 'var(--z-cyan)', stroke: 2.6 }),
                    React.createElement('span', null, toast.msg))))))))
    );
  }

  function ctrlBtn(label, onClick) {
    return React.createElement('div', { onClick, style: { padding: '6px 12px', borderRadius: 999, background: 'rgba(255,255,255,.1)', color: '#fff', fontSize: 12.5, fontWeight: 700, cursor: 'pointer', border: '1px solid rgba(255,255,255,.14)' } }, label);
  }

  function segBtn(label, active, onClick) {
    return React.createElement('div', { key: label, onClick, style: { padding: '5px 12px', borderRadius: 999, background: active ? '#0FA295' : 'transparent', color: active ? '#fff' : 'rgba(255,255,255,.7)', fontSize: 12, fontWeight: 700, cursor: 'pointer' } }, label);
  }

  function Sidebar({ app, railW }) {
    return React.createElement('div', { style: { width: railW, flexShrink: 0, height: '100%', background: 'var(--surface)', borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', padding: '26px 16px 18px' } },
      React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 10, padding: '0 8px 22px' } },
        React.createElement(window.ZMark, { size: 26 }),
        React.createElement(window.ZWordmark, { size: 17, color: 'var(--ink-1)' })),
      React.createElement('div', { style: { display: 'flex', flexDirection: 'column', gap: 4, flex: 1 } },
        NAVITEMS.map(([ic, lb]) => {
          const on = app.tab === NAVMAP[ic];
          return React.createElement('div', { key: ic, onClick: () => app.nav.tab(NAVMAP[ic]), style: { display: 'flex', alignItems: 'center', gap: 13, padding: '13px 14px', borderRadius: 14, cursor: 'pointer', background: on ? 'rgba(15,162,149,.12)' : 'transparent', color: on ? 'var(--brand)' : 'var(--ink-2)' } },
            React.createElement(window.ZIcon, { name: ic, size: 22, stroke: on ? 2.1 : 1.7 }),
            React.createElement('span', { style: { fontSize: 15, fontWeight: on ? 700 : 600 } }, lb));
        })),
      React.createElement('div', { onClick: () => app.nav.tab('me'), style: { display: 'flex', alignItems: 'center', gap: 11, padding: '10px 8px', borderTop: '1px solid var(--line)', cursor: 'pointer' } },
        React.createElement(window.Avatar, { size: 38, ring: 'var(--brand)' }),
        React.createElement('div', { style: { flex: 1, minWidth: 0 } },
          React.createElement('div', { style: { fontSize: 13.5, fontWeight: 700, color: 'var(--ink-1)' } }, 'William A.'),
          React.createElement('div', { style: { fontSize: 11.5, color: 'var(--ink-3)' } }, 'Tier 3'))));
  }

  ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
})();

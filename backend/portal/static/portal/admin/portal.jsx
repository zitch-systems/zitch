// Zitch Admin — app shell: login, sidebar, topbar, routing, live data refresh
const { useState, useEffect } = React;

function Login({ onDone }) {
  const [ident, setIdent] = useState('');
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr('');
    try { await ZAPI.login(ident, pw); onDone(); }
    catch (ex) { setErr(ex.message); }
    setBusy(false);
  };
  return (
    <div className="login-veil">
      <form className="card login-card" onSubmit={submit}>
        <img src="/static/portal/assets/brand/zitch-ribbon2.png" alt="Zitch" style={{ width: 42, margin: '0 auto 10px' }} />
        <div className="card-title" style={{ textAlign: 'center' }}>ZITCH <em>Admin</em></div>
        <p className="dim sm" style={{ textAlign: 'center', margin: '4px 0 16px' }}>Staff sign-in · every action is audit-logged</p>
        <label className="f-label">Email / username</label>
        <input className="f-input" value={ident} onChange={(e) => setIdent(e.target.value)} autoFocus />
        <label className="f-label">Password</label>
        <input className="f-input" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <div className="note warn" style={{ marginTop: 10 }}><Icon name="alert" size={14} /> {err}</div>}
        <button className="btn primary w100" style={{ marginTop: 14 }} disabled={busy || !ident || !pw}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  );
}

function AdminApp() {
  const [authed, setAuthed] = useState(!!ZAPI.token);
  const [view, setView] = useState('overview');
  const [ver, setVer] = useState(0);          // bump -> views re-read window.ZADM
  const [loading, setLoading] = useState(false);
  const [toasts, setToasts] = useState([]);

  const toast = (text) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3600);
  };

  const refresh = async (v) => {
    setLoading(true);
    try { await ZAPI.loadView(v || view); setVer((x) => x + 1); }
    catch (ex) { if (ZAPI.token) toast('⚠ ' + ex.message); else setAuthed(false); }
    setLoading(false);
  };

  useEffect(() => { if (authed) refresh(view); }, [authed, view]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const me = ZAPI.me || { role: 'read_only', caps: {}, name: '' };

  const NAV = [
    { key: 'overview', label: 'Overview', icon: 'home' },
    { key: 'users', label: 'Users & KYC', icon: 'users' },
    { key: 'kyc', label: 'KYC queue', icon: 'shield' },
    { key: 'txns', label: 'Transactions', icon: 'txns' },
    { key: 'fx', label: 'FX & Treasury', icon: 'fx' },
    { key: 'products', label: 'Products', icon: 'card' },
    { key: 'wa', label: 'WhatsApp', icon: 'chat' },
    { key: 'broadcasts', label: 'Broadcasts', icon: 'megaphone' },
    { key: 'ai', label: 'AI controls', icon: 'spark' },
    { key: 'recon', label: 'Providers & recon', icon: 'refresh' },
    { key: 'audit', label: 'Audit log', icon: 'file' },
    { key: 'settings', label: 'Settings & team', icon: 'gear' },
  ];

  const VIEWS = {
    overview: Overview, users: Users, kyc: KycQueue, txns: Transactions, fx: Fx, products: Products,
    wa: WaInbox, broadcasts: Broadcasts, ai: AiControls, recon: Recon, audit: Audit, settings: Settings,
  };
  const View = VIEWS[view];
  const ctx = { role: me.role, can: me.caps || {} };

  return (
    <RoleCtx.Provider value={ctx}>
      <div className="shell" data-screen-label={'Admin · ' + view}>
        <aside className="side">
          <div className="side-brand">
            <img src="/static/portal/assets/brand/zitch-ribbon2.png" alt="Zitch" />
            <span>ZITCH <em>Admin</em></span>
          </div>
          <nav className="side-nav">
            {NAV.map((n) => (
              <button key={n.key} className={'side-link' + (view === n.key ? ' on' : '')} onClick={() => setView(n.key)}>
                <Icon name={n.icon} size={17} /> {n.label}
              </button>
            ))}
          </nav>
          <div className="side-foot">
            <a className="side-link" href="/"><Icon name="logout" size={16} /> Back to site</a>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="env-pill"><span className="dot"></span> {loading ? 'Loading…' : 'Live'}</div>
            <div style={{ flex: 1 }}></div>
            <label className="role-pick">
              <Icon name="shield" size={15} />
              <span className="sm dim">Role</span>
              <b style={{ fontSize: 13 }}>{me.role}</b>
            </label>
            <div className="me">
              <span className="avatar">{(me.name || '?').split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()}</span> {me.name}
            </div>
            <button className="icon-btn" title="Sign out" onClick={() => { ZAPI.logout(); setAuthed(false); }}>
              <Icon name="logout" size={16} />
            </button>
          </header>
          <main className="content">
            <View key={ver} toast={toast} refresh={() => refresh(view)} />
          </main>
        </div>
        <ToastHost toasts={toasts} />
      </div>
    </RoleCtx.Provider>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<AdminApp />);

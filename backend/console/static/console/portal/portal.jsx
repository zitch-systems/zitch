// Zitch Admin — app shell: sidebar, topbar, role switcher, routing
const { useState, useEffect } = React;

function initials(name) {
  return (name || 'OP').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || 'OP';
}

function AdminApp({ me, initialRole, onSignOut }) {
  const [view, setView] = useState('overview');
  const [role, setRole] = useState(initialRole || (me && me.role) || 'read_only');
  const [toasts, setToasts] = useState([]);

  const toast = (text) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  };

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
  const ctx = { role, can: CAN[role] };

  return (
    <RoleCtx.Provider value={ctx}>
      <div className="shell" data-screen-label={'Admin · ' + view}>
        <aside className="side">
          <div className="side-brand">
            <img src="/static/console/assets/brand/zitch-ribbon2.png" alt="Zitch" />
            <span>ZITCH <em>Admin</em></span>
          </div>
          <nav className="side-nav">
            {NAV.map((n) => (
              <button key={n.key} className={'side-link' + (view === n.key ? ' on' : '')} onClick={() => setView(n.key)}>
                <Icon name={n.icon} size={17} /> {n.label}
                {n.key === 'wa' && <span className="side-pill">1</span>}
              </button>
            ))}
          </nav>
          <div className="side-foot">
            <a className="side-link" href="/"><Icon name="logout" size={16} /> Back to site</a>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="env-pill"><span className="dot"></span> Production</div>
            <div style={{ flex: 1 }}></div>
            {(me && me.role === 'super_admin') ? (
              <label className="role-pick">
                <Icon name="shield" size={15} />
                <span className="sm dim">Viewing as</span>
                <select value={role} onChange={(e) => setRole(e.target.value)}>
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </label>
            ) : (
              <span className="env-pill" style={{ background: '#EEF1F4', color: '#4A4E57' }}>
                <Icon name="shield" size={13} /> {role}
              </span>
            )}
            <div className="me"><span className="avatar">{initials(me && me.name)}</span> {(me && me.name) || 'Operator'}</div>
            <button className="btn ghost sm-btn" onClick={onSignOut}><Icon name="logout" size={14} /> Sign out</button>
          </header>
          <main className="content">
            <View toast={toast} />
          </main>
        </div>
        <ToastHost toasts={toasts} />
      </div>
    </RoleCtx.Provider>
  );
}

// --- Auth gate: staff sign-in → load real data → render the shell ----------
function Login({ onSignedIn }) {
  const [u, setU] = useState('');
  const [p, setP] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setErr(''); setBusy(true);
    try {
      const res = await ZADM_API.login(u.trim(), p);
      ZADM_API.setToken(res.token);
      onSignedIn(res);
    } catch (ex) {
      setErr(ex.message || 'Sign in failed');
    } finally { setBusy(false); }
  };
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'var(--navy)' }}>
      <form onSubmit={submit} className="card" style={{ width: 360, padding: 28 }}>
        <div className="side-brand" style={{ padding: 0, marginBottom: 8, color: 'var(--navy)' }}>
          <img src="/static/console/assets/brand/zitch-ribbon2.png" alt="Zitch" style={{ height: 26 }} />
          <span style={{ letterSpacing: '.14em', fontWeight: 700 }}>ZITCH <em style={{ fontStyle: 'normal', color: 'var(--teal-deep)' }}>Admin</em></span>
        </div>
        <p className="dim sm" style={{ margin: '0 0 16px' }}>Operator sign in</p>
        <label className="f-label">Username or email</label>
        <input className="f-input" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
        <label className="f-label">Password</label>
        <input className="f-input" type="password" value={p} onChange={(e) => setP(e.target.value)} />
        {err && <div className="note warn" style={{ marginTop: 12 }}>{err}</div>}
        <button className="btn primary" style={{ marginTop: 18, width: '100%' }} disabled={busy || !u || !p}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p className="dim sm" style={{ marginTop: 14, textAlign: 'center' }}>Staff accounts only · every action is audited</p>
      </form>
    </div>
  );
}

function Root() {
  const [phase, setPhase] = useState('init'); // init | login | loading | ready | error
  const [me, setMe] = useState(null);
  const [error, setError] = useState('');

  const load = async () => {
    setPhase('loading');
    try {
      const [meRes, boot] = await Promise.all([ZADM_API.me(), ZADM_API.bootstrap()]);
      ZADM.applyBootstrap(boot);
      setMe(meRes);
      setPhase('ready');
    } catch (ex) {
      if (ex.status === 401) { ZADM_API.setToken(''); setPhase('login'); }
      else { setError(ex.message || 'Could not load the portal'); setPhase('error'); }
    }
  };

  useEffect(() => {
    if (ZADM_API.getToken()) load();
    else setPhase('login');
  }, []);

  const signOut = async () => { await ZADM_API.logout(); setMe(null); setPhase('login'); };

  if (phase === 'login') return <Login onSignedIn={() => load()} />;
  if (phase === 'error') {
    return (
      <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
        <div className="card" style={{ padding: 28, maxWidth: 420, textAlign: 'center' }}>
          <div className="card-title" style={{ marginBottom: 8 }}>Couldn’t load the portal</div>
          <p className="dim sm">{error}</p>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', marginTop: 14 }}>
            <button className="btn primary" onClick={load}>Retry</button>
            <button className="btn ghost" onClick={signOut}>Sign out</button>
          </div>
        </div>
      </div>
    );
  }
  if (phase !== 'ready') {
    return <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', color: 'var(--t3)' }}>Loading operator portal…</div>;
  }
  return <AdminApp me={me} initialRole={me && me.role} onSignOut={signOut} />;
}

ReactDOM.createRoot(document.getElementById('root')).render(<Root />);

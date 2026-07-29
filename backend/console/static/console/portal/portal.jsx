// Zitch Admin (demo bundle) — app shell: sidebar, topbar, role switcher, routing
// Served only at /portal/?mode=demo. The live shell is static/portal/admin/portal.jsx.
const { useState } = React;

function initials(name) {
  return (name || 'OP').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || 'OP';
}

function AdminApp({ me, initialRole, onSignOut }) {
  const [view, setView] = useState('overview');
  const [role, setRole] = useState(initialRole || (me && me.role) || 'read_only');
  const [toasts, setToasts] = useState([]);
  // Bumping dataV remounts the active view, so it re-reads the ZADM collections
  // (views capture them in useState on mount).
  const [dataV, setDataV] = useState(0);

  // Marked at this one point rather than at the ~30 call sites that raise a
  // toast, so a view added later cannot forget to say which portal it is.
  const toast = (text) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text: 'Demo — ' + text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3200);
  };

  // There is nothing to refresh from — the fixture is the whole data layer.
  // Remount so the control still demonstrates the interaction, and say plainly
  // that nothing was fetched rather than toasting a "Data refreshed" that would
  // read exactly like the live portal's.
  const refresh = () => {
    setDataV((v) => v + 1);
    toast('Demo data — nothing to fetch');
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
            {/* Hardcoded "Production" used to sit here, in the one bundle that
                can never show production. Styled by body.is-demo in admin.html. */}
            <div className="env-pill"><span className="dot"></span> Demo data</div>
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
            <button className="btn ghost sm-btn" onClick={refresh}>
              <Icon name="refresh" size={14} /> Refresh
            </button>
            <div className="me"><span className="avatar">{initials(me && me.name)}</span> {(me && me.name) || 'Operator'}</div>
            {/* No session to end here — this leaves the demo for the live portal. */}
            <button className="btn ghost sm-btn" onClick={onSignOut}><Icon name="logout" size={14} /> Exit demo</button>
          </header>
          <main className="content">
            <View key={view + ':' + dataV} toast={toast} />
          </main>
        </div>
        <ToastHost toasts={toasts} />
      </div>
    </RoleCtx.Provider>
  );
}

// --- No auth gate: fixtures only, so there is nothing to sign into ---------
// The live bundle gates on a staff token before it will render. This one has no
// API to authenticate against, so it goes straight to the shell as a fictional
// operator.
//
// Dropping the sign-in form is the point, not a shortcut. The old one was a
// real login: it posted whatever you typed to the live staff endpoint and kept
// the returned bearer token. Even made inert it would be the wrong shape — a
// password field on a page stamped DEMO trains operators to type live
// credentials into a mock, which is the habit an attacker only has to imitate.
// The demo has no field to type them into now.
//
// super_admin is deliberate: the role picker is the most useful thing to
// demonstrate, and every capability it unlocks here is a fixture. The live
// portal resolves the role server-side on every call and never trusts this.
const DEMO_ME = { name: 'Demo Operator', email: 'demo@zitch.example', role: 'super_admin' };

function Root() {
  // "Exit demo" leaves for the live portal rather than clearing a session that
  // does not exist. Full page load: the two bundles share component names, so
  // the mode is a navigation, never a client-side swap.
  const exit = () => { window.location.href = '/portal/'; };
  return <AdminApp me={DEMO_ME} initialRole={DEMO_ME.role} onSignOut={exit} />;
}

ReactDOM.createRoot(document.getElementById('root')).render(<Root />);
